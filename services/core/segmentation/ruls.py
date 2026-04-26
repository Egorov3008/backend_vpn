from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Callable

from .base import BaseCondition, Condition
from logger import logger
from .model import UserSegment
from models import Key, User


class SimpleCondition(BaseCondition):
    """Условие, основанное на списке лямбд."""

    def __init__(self, *rules: Callable[[User, List[Key]], bool]):
        super().__init__()
        self.rules = rules  # теперь кортеж — неизменяемый

    async def check(self, user_data: Dict[str, Any]) -> bool:
        try:
            user, keys = self._get_user_and_keys(user_data)
            return all(rule(user, keys) for rule in self.rules)
        except Exception as e:
            logger.error(
                "Ошибка при проверке условия", error=str(e), user_data=user_data
            )
            return False


# === Конструкторы условий ===


def new_user_condition() -> Condition:
    """Новый пользователь: trial=0, нет ключей, регистрация ≤15 дней назад."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=15)
    return SimpleCondition(
        lambda user, keys: user.trial == 0,
        lambda user, keys: len(keys) == 0,
        lambda user, keys: user.created_at >= cutoff,
    )


def expiring_keys_condition(hours: int = 24) -> Condition:
    """Ключи заканчиваются в ближайшие N часов."""
    return SimpleCondition(
        lambda user, keys: any(
            key.expiry_time - datetime.now(timezone.utc).timestamp() * 1000
            <= hours * 3600 * 1000
            for key in keys
        )
    )


def inactive_trial_condition() -> Condition:
    """Неактивный триал: trial=1, неиспользуемый trial-ключ старше 2 дней и 0 Гб."""
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp() * 1000
    return SimpleCondition(
        lambda user, keys: user.trial == 1,
        lambda user, keys: any(
            key.tariff_id == 10
            and key.created_at < two_days_ago
            and key.total_gb == 0.0
            for key in keys
        ),
    )


def cold_lead_condition() -> Condition:
    """Холодный лид: trial=0, нет ключей, регистрация >30 дней назад."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    return SimpleCondition(
        lambda user, keys: user.trial == 0,
        lambda user, keys: len(keys) == 0,
        lambda user, keys: user.created_at < cutoff,
    )


def blocked_condition() -> Condition:
    return SimpleCondition(lambda user, keys: user.is_blocked == True)


# === Сегментатор ===


class UserSegmenter:
    """Определяет сегмент пользователя на основе правил."""

    def __init__(self):
        self.rules = [
            (UserSegment.EXPIRING_SOON, expiring_keys_condition(10)),
            (UserSegment.EXPIRING_SOON, expiring_keys_condition(24)),
            (UserSegment.NEW_USER, new_user_condition()),
            (UserSegment.INACTIVE_TRIAL, inactive_trial_condition()),
            (UserSegment.COLD_LEAD, cold_lead_condition()),
        ]
        self._cache: Dict[str, UserSegment] = {}

    async def determine_segment(self, user_data: Dict[str, Any]) -> UserSegment:
        user: User = user_data.get("user")
        if not user:
            return UserSegment.INACTIVE

        # Кэшируем результат на 5 минут
        cache_key = f"{user.tg_id}_{int(datetime.now().timestamp() // 300)}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        for segment, condition in self.rules:
            if await condition.check(user_data):
                self._cache[cache_key] = segment
                return segment

        self._cache[cache_key] = UserSegment.INACTIVE
        return UserSegment.INACTIVE
