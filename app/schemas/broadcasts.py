import re
from dataclasses import asdict
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from services.broadcasts.audiences import find_audience
from services.broadcasts.media import ALBUM_MAX, album_error
from services.broadcasts.models import Broadcast, MediaType

MediaTypeName = Literal["photo", "video", "animation", "audio", "document"]

# Подпись к медиа у Telegram до 1024 символов, текст — до 4096 (считаются без HTML-тегов).
CAPTION_MAX = 1024
TEXT_MAX = 4096
_TAG = re.compile(r"<[^>]+>")


def visible_length(html_text: str) -> int:
    return len(_TAG.sub("", html_text))


class BroadcastMediaIn(BaseModel):
    type: MediaTypeName
    file_id: str = Field(..., min_length=10, max_length=255, pattern=r"^[A-Za-z0-9_\-]+$")
    file_name: Optional[str] = Field(None, max_length=255)


class BroadcastButtonIn(BaseModel):
    kind: Literal["url", "miniapp"]
    text: str = Field(..., min_length=1, max_length=64)
    url: Optional[str] = Field(None, max_length=2048, description="Только для kind=url: https://… или tg://…")

    @model_validator(mode="after")
    def _check_url(self):
        if self.kind == "url" and not (self.url or "").startswith(("https://", "http://", "tg://")):
            raise ValueError("Ссылка кнопки должна начинаться с https://, http:// или tg://")
        return self


class BroadcastMessageIn(BaseModel):
    text: str = Field("", max_length=TEXT_MAX * 4, description="HTML: <b>, <i>, <u>, <s>, <a href>, <code>")
    media: Optional[BroadcastMediaIn] = Field(None, description="Один файл (старый формат; то же, что media_items из одного)")
    media_items: list[BroadcastMediaIn] = Field(
        default_factory=list, max_length=ALBUM_MAX, description="Файлы по порядку; 2–10 — альбом"
    )
    button: Optional[BroadcastButtonIn] = None
    disable_notification: bool = False
    pin: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.media is not None:
            if self.media_items:
                raise ValueError("Передайте файлы либо в media, либо в media_items")
            self.media_items, self.media = [self.media], None
        problem = album_error([MediaType(m.type) for m in self.media_items])
        if problem:
            raise ValueError(problem)
        self.text = self.text.strip()
        if not self.text and not self.media_items:
            raise ValueError("Нужен текст или файл")
        album_with_button = len(self.media_items) > 1 and self.button is not None
        if album_with_button and not self.text:
            raise ValueError("К альбому кнопка уходит отдельным сообщением с текстом: напишите текст")
        # Подпись к файлу короче; у альбома с кнопкой текст — отдельное сообщение
        is_caption = bool(self.media_items) and not album_with_button
        limit = CAPTION_MAX if is_caption else TEXT_MAX
        if visible_length(self.text) > limit:
            where = "Подпись к файлу" if is_caption else "Текст"
            raise ValueError(f"{where} длиннее {limit} символов")
        return self


class BroadcastCreateRequest(BroadcastMessageIn):
    audience: str = Field("all", max_length=32)


class AudienceResponse(BaseModel):
    id: str
    title: str
    count: int


class AudiencesResponse(BaseModel):
    audiences: list[AudienceResponse]


class MediaUploadResponse(BaseModel):
    type: MediaTypeName
    file_id: str
    file_name: Optional[str]
    size: int


class BroadcastMediaItemResponse(BaseModel):
    type: MediaTypeName
    file_name: Optional[str]


class BroadcastProgressResponse(BaseModel):
    total: int
    pending: int
    sent: int
    blocked: int
    failed: int


class BroadcastResponse(BaseModel):
    id: int
    admin_tg_id: Optional[int]
    audience: str
    audience_title: str
    status: Literal["running", "cancel_requested", "canceled", "done", "failed"]
    error: Optional[str]
    created_at: datetime
    finished_at: Optional[datetime]
    text: str
    media_type: Optional[MediaTypeName]  # первый файл
    media_file_name: Optional[str]
    media_items: list[BroadcastMediaItemResponse]
    button_kind: Optional[str]
    button_text: Optional[str]
    button_url: Optional[str]
    disable_notification: bool
    pin: bool
    progress: BroadcastProgressResponse

    @classmethod
    def build(cls, b: Broadcast) -> "BroadcastResponse":
        audience = find_audience(b.audience)
        m = b.message
        return cls(
            id=b.id,
            admin_tg_id=b.admin_tg_id,
            audience=b.audience,
            audience_title=audience.title if audience else b.audience,
            status=b.status.value,
            error=b.error,
            created_at=b.created_at,
            finished_at=b.finished_at,
            text=m.text,
            media_type=m.media[0].type.value if m.media else None,
            media_file_name=m.media[0].file_name if m.media else None,
            media_items=[BroadcastMediaItemResponse(type=x.type.value, file_name=x.file_name) for x in m.media],
            button_kind=m.button.kind if m.button else None,
            button_text=m.button.text if m.button else None,
            button_url=m.button.url if m.button else None,
            disable_notification=m.disable_notification,
            pin=m.pin,
            progress=BroadcastProgressResponse(**asdict(b.progress)),
        )


class BroadcastsListResponse(BaseModel):
    broadcasts: list[BroadcastResponse]
