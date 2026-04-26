from datetime import datetime
from typing import List

from .base import BaseCondition
from .key_model import KeySegment
from logger import logger
from models import Key


class KeyCondition(BaseCondition):
    """Условие для проверки сегмента ключа."""

    async def check(self, user_data=None) -> bool:
        """Реализация базового интерфейса (не используется для ключей)."""
        raise NotImplementedError

    async def check_key(self, key: Key) -> bool:
        """Проверить, принадлежит ли ключ этому сегменту."""
        raise NotImplementedError


class ExpiringIn10HCondition(KeyCondition):
    """Ключ истекает в ближайшие 10 часов."""

    async def check_key(self, key: Key) -> bool:
        threshold = self.time.now_ms + 10 * 3600 * 1000
        return self.time.now_ms < key.expiry_time <= threshold


class ExpiringIn24HCondition(KeyCondition):
    """Ключ истекает в ближайшие 24 часа."""

    async def check_key(self, key: Key) -> bool:
        threshold = self.time.now_ms + 24 * 3600 * 1000
        return self.time.now_ms < key.expiry_time <= threshold


class ExpiringIn7DCondition(KeyCondition):
    """Ключ истекает в ближайшие 7 дней."""

    async def check_key(self, key: Key) -> bool:
        threshold = self.time.now_ms + 7 * 24 * 3600 * 1000
        return self.time.now_ms < key.expiry_time <= threshold


class ExpiringIn30DCondition(KeyCondition):
    """Ключ истекает в ближайшие 30 дней."""

    async def check_key(self, key: Key) -> bool:
        threshold = self.time.now_ms + 30 * 24 * 3600 * 1000
        return self.time.now_ms < key.expiry_time <= threshold


class ExpiredCondition(KeyCondition):
    """Устаревший ключ."""

    async def check_key(self, key: Key) -> bool:
        return key.expiry_time < self.time.now_ms


class ActiveCondition(BaseCondition):
    """Активный ключ — неистёкший платный ключ с использованием трафика (used_traffic > 0)."""

    async def check(self, user_data=None) -> bool:
        """Реализация базового интерфейса (не используется для ключей)."""
        raise NotImplementedError

    async def check_key(self, key: Key) -> bool:
        if key.tariff_id is None:
            return False
        is_paid = self._is_paid_tariff(key.tariff_id)
        is_not_expired = key.expiry_time >= self.time.now_ms
        has_usage = key.used_traffic is not None and key.used_traffic > 0
        return is_paid and is_not_expired and has_usage


class TrialCondition(KeyCondition):
    """Trial ключ (tariff_id == 10)."""

    async def check_key(self, key: Key) -> bool:
        return key.tariff_id == 10


class UnusedCondition(KeyCondition):
    """Неиспользуемый ключ — неистёкший ключ с used_traffic == 0."""

    async def check_key(self, key: Key) -> bool:
        is_not_expired = key.expiry_time >= self.time.now_ms
        no_usage = key.used_traffic == 0 or key.used_traffic is None
        return is_not_expired and no_usage


class AllCondition(KeyCondition):
    """Все ключи."""

    async def check_key(self, key: Key) -> bool:
        return True


# === Сегментатор для ключей ===


class KeySegmenter:
    """Определяет сегмент ключа на основе правил.

    Порядок проверки (first-match):
    1. EXPIRED - истекший ключ
    2. EXPIRING_10H - истекает в ближайшие 10 часов (приоритет для уведомлений)
    3. EXPIRING_24H - истекает в ближайшие 24 часа
    4. EXPIRING_7D - истекает в ближайшие 7 дней
    5. EXPIRING_30D - истекает в ближайшие 30 дней
    6. TRIAL - trial ключи (не истекающие)
    7. UNUSED - неистёкшие ключи с used_traffic == 0
    8. ACTIVE - активные платные ключи с used_traffic > 0
    9. ALL - остальные
    """

    def __init__(self):
        self.rules = [
            (KeySegment.EXPIRED, ExpiredCondition()),
            # EXPIRING проверяются до TRIAL и ACTIVE (first-match для уведомлений)
            (KeySegment.EXPIRING_10H, ExpiringIn10HCondition()),
            (KeySegment.EXPIRING_24H, ExpiringIn24HCondition()),
            (KeySegment.EXPIRING_7D, ExpiringIn7DCondition()),
            (KeySegment.EXPIRING_30D, ExpiringIn30DCondition()),
            # TRIAL для ключей далеко в будущем
            (KeySegment.TRIAL, TrialCondition()),
            # UNUSED до ACTIVE (чтобы ключи без трафика не попадали в ACTIVE)
            (KeySegment.UNUSED, UnusedCondition()),
            (KeySegment.ACTIVE, ActiveCondition()),
        ]
        self._cache: dict = {}

    async def determine_segment(self, key: Key) -> KeySegment:
        """Определить сегмент ключа на основе условий."""
        try:
            # Кэшируем результат на 5 минут
            cache_key = f"{key.email}_{int(datetime.now().timestamp() // 300)}"
            if cache_key in self._cache:
                return self._cache[cache_key]

            for segment, condition in self.rules:
                if await condition.check_key(key):
                    self._cache[cache_key] = segment
                    return segment

            # По умолчанию - ALL
            self._cache[cache_key] = KeySegment.ALL
            return KeySegment.ALL
        except Exception as e:
            logger.error(
                "Ошибка при определении сегмента ключа",
                error=str(e),
                key_email=key.email,
            )
            return KeySegment.ALL

    async def filter_keys(self, keys: List[Key], segment: KeySegment) -> List[Key]:
        """Фильтровать ключи по сегменту."""
        if segment == KeySegment.ALL:
            return keys

        filtered = []
        for key in keys:
            key_segment = await self.determine_segment(key)
            if key_segment == segment:
                filtered.append(key)

        return filtered
