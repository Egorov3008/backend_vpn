"""Маппинг сегментов ключей → воронки уведомлений."""

from enum import Enum


class KeySegment(Enum):
    """Сегменты ключей."""

    EXPIRING_10H = "expiring_10h"
    EXPIRING_24H = "expiring_24h"
    EXPIRING_7D = "expiring_7d"
    EXPIRING_30D = "expiring_30d"
    EXPIRED = "expired"
    ACTIVE = "active"
    TRIAL = "trial"
    UNUSED = "unused"
    ALL = "all"


# Разрешённое временное окно отправки (часы, включительно-исключительно)
SENDING_HOUR_WINDOW: tuple[int, int] = (8, 23)

# Маппинг: сегмент ключа → funnel_id (key-based воронки)
KEY_SEGMENT_TO_FUNNEL: dict[KeySegment, str] = {
    KeySegment.EXPIRING_10H: "key_expiry_10h",
    KeySegment.EXPIRING_24H: "key_expiry_24h",
    KeySegment.TRIAL: "trial_unused",
}

# Обратный маппинг: funnel_id → KeySegment
SEGMENT_BY_FUNNEL: dict[str, KeySegment] = {
    v: k for k, v in KEY_SEGMENT_TO_FUNNEL.items()
}
