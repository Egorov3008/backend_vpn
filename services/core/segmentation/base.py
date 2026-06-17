from abc import ABC, abstractmethod
from typing import List, Callable, Dict, Any

from models import Key, User
from services.core.time_helper import TimeHelper


class Condition(ABC):
    """Абстрактный класс условия проверки сегмента."""

    @abstractmethod
    async def check(self, user_data: Dict[str, Any]) -> bool:
        pass


class BaseCondition(Condition):
    """Базовый класс с общими утилитами."""

    def __init__(self):
        self.time = TimeHelper()

    def _get_user_and_keys(self, user_data: Dict[str, Any]) -> tuple[User, List[Key]]:
        """Безопасное извлечение пользователя и ключей."""
        user = user_data.get("user")
        keys = user_data.get("keys", [])
        if not user:
            raise ValueError("user_data должен содержать 'user'")
        return user, keys

    def _is_paid_tariff(self, tariff_id: int) -> bool:
        """Проверка, является ли тариф платным (не trial)."""
        return tariff_id != 10

    def _filter_keys(
        self,
        keys: List[Key],
        *,
        tariff_filter: Callable[[int], bool] = None,
        expiry_filter: Callable[[int], bool] = None,
        usage_filter: Callable[[float], bool] = None,
    ) -> List[Key]:
        """Универсальный фильтр ключей."""
        result = []
        for key in keys:
            if tariff_filter and not tariff_filter(key.tariff_id):
                continue
            if expiry_filter and not expiry_filter(key.expiry_time):
                continue
            if usage_filter and not usage_filter(key.used_traffic):
                continue
            result.append(key)
        return result

    def get_active_paid_keys(self, keys: List[Key]) -> List[Key]:
        return self._filter_keys(
            keys,
            tariff_filter=self._is_paid_tariff,
            expiry_filter=lambda exp: exp >= self.time.now_ms,
        )

    def get_expired_paid_keys(self, keys: List[Key]) -> List[Key]:
        return self._filter_keys(
            keys,
            tariff_filter=self._is_paid_tariff,
            expiry_filter=lambda exp: exp < self.time.now_ms,
        )

    def get_keys_expiring_in(self, keys: List[Key], hours: int) -> List[Key]:
        threshold = self.time.now_ms + hours * 3600 * 1000
        return self._filter_keys(
            keys,
            expiry_filter=lambda exp: self.time.now_ms < exp <= threshold,
        )
