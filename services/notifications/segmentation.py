"""Сегментация ключей для notification funnels (backend)."""

from datetime import datetime
from enum import Enum
from typing import List

from logger import logger
from models.keys.key import Key


class KeySegment(Enum):
    EXPIRING_10H = "expiring_10h"
    EXPIRING_24H = "expiring_24h"
    EXPIRING_7D = "expiring_7d"
    EXPIRING_30D = "expiring_30d"
    EXPIRED = "expired"
    ACTIVE = "active"
    TRIAL = "trial"
    UNUSED = "unused"
    ALL = "all"


class KeySegmenter:
    """Определяет сегмент ключа на основе правил (first-match)."""

    TRIAL_TARIFF_ID = 10

    def __init__(self):
        self._now_ms: int = 0

    def _refresh_time(self):
        self._now_ms = int(datetime.now().timestamp() * 1000)

    def filter_keys(self, keys: List[Key], segment: KeySegment) -> List[Key]:
        """Отфильтровать ключи по сегменту."""
        self._refresh_time()
        if segment == KeySegment.ALL:
            return keys
        result = []
        for key in keys:
            if self._matches(key, segment):
                result.append(key)
        return result

    def _matches(self, key: Key, segment: KeySegment) -> bool:
        now = self._now_ms
        if segment == KeySegment.EXPIRED:
            return key.expiry_time < now
        if segment == KeySegment.EXPIRING_10H:
            return now < key.expiry_time <= now + 10 * 3600 * 1000
        if segment == KeySegment.EXPIRING_24H:
            return now < key.expiry_time <= now + 24 * 3600 * 1000
        if segment == KeySegment.EXPIRING_7D:
            return now < key.expiry_time <= now + 7 * 24 * 3600 * 1000
        if segment == KeySegment.EXPIRING_30D:
            return now < key.expiry_time <= now + 30 * 24 * 3600 * 1000
        if segment == KeySegment.TRIAL:
            return key.tariff_id == self.TRIAL_TARIFF_ID and key.expiry_time >= now
        if segment == KeySegment.UNUSED:
            is_not_expired = key.expiry_time >= now
            no_usage = key.used_traffic == 0 or key.used_traffic is None
            return is_not_expired and no_usage
        if segment == KeySegment.ACTIVE:
            is_paid = key.tariff_id != self.TRIAL_TARIFF_ID
            is_not_expired = key.expiry_time >= now
            has_usage = key.used_traffic is not None and key.used_traffic > 0
            return is_paid and is_not_expired and has_usage
        return False
