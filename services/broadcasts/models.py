"""Данные рассылки: что отправляем, кому и чем закончилась отправка одному человеку."""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class MediaType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    ANIMATION = "animation"
    AUDIO = "audio"
    DOCUMENT = "document"


@dataclass(frozen=True)
class Media:
    type: MediaType
    # file_id пользовательского бота: файл один раз загружается в Telegram
    # (POST /admin/broadcasts/media), дальше все получают его по file_id.
    file_id: str
    file_name: Optional[str] = None


@dataclass(frozen=True)
class Button:
    """Одна inline-кнопка под сообщением: ссылка или Mini App (MINIAPP_URL)."""
    kind: str  # "url" | "miniapp"
    text: str
    url: str


@dataclass(frozen=True)
class BroadcastMessage:
    text: str = ""  # HTML (parse_mode=HTML); с медиа — подпись
    media: Optional[Media] = None
    button: Optional[Button] = None
    disable_notification: bool = False
    pin: bool = False


class DeliveryStatus(str, Enum):
    SENT = "sent"
    BLOCKED = "blocked"  # бот заблокирован, аккаунт удалён или чат не найден
    FAILED = "failed"


@dataclass(frozen=True)
class SendResult:
    """Ответ Telegram на одну отправку.

    retry_after — повторить через столько секунд (429 или сбой сети, тогда
    network_error); unauthorized — токен бота не принят (401), рассылку дальше
    вести бессмысленно.
    """
    status: DeliveryStatus
    error: Optional[str] = None
    message_id: Optional[int] = None
    retry_after: Optional[float] = None
    unauthorized: bool = False
    network_error: bool = False

    @property
    def ok(self) -> bool:
        return self.status == DeliveryStatus.SENT


class BroadcastStatus(str, Enum):
    RUNNING = "running"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    DONE = "done"
    FAILED = "failed"


ACTIVE_STATUSES = (BroadcastStatus.RUNNING.value, BroadcastStatus.CANCEL_REQUESTED.value)


@dataclass(frozen=True)
class Progress:
    total: int = 0
    pending: int = 0
    sent: int = 0
    blocked: int = 0
    failed: int = 0


@dataclass(frozen=True)
class Broadcast:
    id: int
    admin_tg_id: Optional[int]
    audience: str
    message: BroadcastMessage
    status: BroadcastStatus
    error: Optional[str]
    created_at: datetime
    finished_at: Optional[datetime]
    progress: Progress
