import re
from dataclasses import asdict
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from services.broadcasts.audiences import find_audience
from services.broadcasts.models import Broadcast

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
    media: Optional[BroadcastMediaIn] = None
    button: Optional[BroadcastButtonIn] = None
    disable_notification: bool = False
    pin: bool = False

    @model_validator(mode="after")
    def _check_text(self):
        self.text = self.text.strip()
        if not self.text and self.media is None:
            raise ValueError("Нужен текст или файл")
        limit = CAPTION_MAX if self.media else TEXT_MAX
        if visible_length(self.text) > limit:
            where = "Подпись к файлу" if self.media else "Текст"
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
    media_type: Optional[MediaTypeName]
    media_file_name: Optional[str]
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
            media_type=m.media.type.value if m.media else None,
            media_file_name=m.media.file_name if m.media else None,
            button_kind=m.button.kind if m.button else None,
            button_text=m.button.text if m.button else None,
            button_url=m.button.url if m.button else None,
            disable_notification=m.disable_notification,
            pin=m.pin,
            progress=BroadcastProgressResponse(**asdict(b.progress)),
        )


class BroadcastsListResponse(BaseModel):
    broadcasts: list[BroadcastResponse]
