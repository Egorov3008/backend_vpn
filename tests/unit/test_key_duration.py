"""Unit-тесты для ExpiryCalculator.key_duration.

Проверяют:
- Обычное продление: max(now, expiry) + tariff.period.
- Продление триального ключа: now + (tariff.period + remaining_trial_days).
- Граничные случаи: истёкший триал, много месяцев.

Все ожидания считаются от фактического ``datetime.now()`` внутри тестируемой
функции, чтобы избежать расхождений из-за миллисекунд между вызовами.
"""
from datetime import datetime, timezone

import pytest

from services.core.keys.utils.calculator import (
    ExpiryCalculator,
    TRIAL_PERIOD_DAYS,
    TRIAL_TARIFF_ID,
)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _key(tariff_id: int, created_at_ms: int, expiry_time_ms: int):
    return type(
        "K",
        (),
        {
            "tariff_id": tariff_id,
            "created_at": created_at_ms,
            "expiry_time": expiry_time_ms,
        },
    )()


def _approx(expected_ms: int, tolerance_ms: int = 5000):
    """Возвращает функцию-сравнение с допуском."""
    return lambda actual: abs(actual - expected_ms) < tolerance_ms


class TestKeyDurationRegularKey:
    def test_renewal_when_key_not_expired_adds_30_days_to_expiry(self):
        """Ключ с expiry в будущем: max(expiry, now) = expiry, +30 дней."""
        # Фиксируем now, чтобы expiry было гарантированно в будущем.
        now = datetime.now(timezone.utc)
        future_expiry = now.timestamp() + 7 * 86_400  # +7 дней
        key = _key(
            tariff_id=7,
            created_at_ms=_ms(now),
            expiry_time_ms=int(future_expiry * 1000),
        )
        result_ms = ExpiryCalculator().key_duration(key, days=30, number_of_months=1)
        # Ожидаем future_expiry + 30 дней.
        expected_ms = int(future_expiry * 1000) + int(30 * 86_400_000)
        assert _approx(expected_ms)(result_ms)

    def test_renewal_when_key_expired_extends_from_now(self):
        """Истёкший ключ: max(expiry, now) = now, +30 дней от now."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        key = _key(
            tariff_id=7,
            created_at_ms=now_ms - 10 * 86_400_000,  # 10 дней назад
            expiry_time_ms=now_ms - 5 * 86_400_000,  # истёк 5 дней назад
        )
        result_ms = ExpiryCalculator().key_duration(key, days=30, number_of_months=1)
        expected_ms = now_ms + int(30 * 86_400_000)
        assert _approx(expected_ms)(result_ms)

    def test_renewal_multiple_months(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        key = _key(
            tariff_id=7,
            created_at_ms=now_ms,
            expiry_time_ms=now_ms,
        )
        result_ms = ExpiryCalculator().key_duration(key, days=30, number_of_months=3)
        expected_ms = now_ms + int(90 * 86_400_000)
        assert _approx(expected_ms)(result_ms)


class TestKeyDurationTrialRenewal:
    def test_trial_just_created_renewal_gets_month_plus_full_trial(self):
        """Ключ только что создан — пользователь получает 30 + 7 = 37 дней."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        key = _key(
            tariff_id=TRIAL_TARIFF_ID,
            created_at_ms=now_ms - 2000,  # 2 секунды назад
            expiry_time_ms=now_ms,  # не важно
        )
        result_ms = ExpiryCalculator().key_duration(key, days=30, number_of_months=1)
        expected_ms = now_ms + int((30 + TRIAL_PERIOD_DAYS) * 86_400_000)
        assert _approx(expected_ms)(result_ms)

    def test_trial_partially_used_renewal_keeps_remaining_days(self):
        """Ключ существует 3 дня — пользователь получает 30 + 4 = 34 дня."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        key = _key(
            tariff_id=TRIAL_TARIFF_ID,
            created_at_ms=now_ms - 3 * 86_400_000,
            expiry_time_ms=now_ms,
        )
        result_ms = ExpiryCalculator().key_duration(key, days=30, number_of_months=1)
        expected_ms = now_ms + int((30 + 4) * 86_400_000)
        assert _approx(expected_ms)(result_ms)

    def test_trial_fully_consumed_renewal_only_paid_month(self):
        """Ключ живёт 10 дней (триал 7 уже истёк) — пользователь получает только 30."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        key = _key(
            tariff_id=TRIAL_TARIFF_ID,
            created_at_ms=now_ms - 10 * 86_400_000,
            expiry_time_ms=now_ms,
        )
        result_ms = ExpiryCalculator().key_duration(key, days=30, number_of_months=1)
        expected_ms = now_ms + int(30 * 86_400_000)
        assert _approx(expected_ms)(result_ms)

    def test_trial_renewal_ignores_stale_expiry_time(self):
        """Даже если у триального ключа expiry в прошлом, считаем от now + 30 + remaining."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        key = _key(
            tariff_id=TRIAL_TARIFF_ID,
            created_at_ms=now_ms,
            expiry_time_ms=int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
        )
        result_ms = ExpiryCalculator().key_duration(key, days=30, number_of_months=1)
        year_2020_plus_30 = int(datetime(2020, 1, 31, tzinfo=timezone.utc).timestamp() * 1000)
        assert result_ms > year_2020_plus_30
        expected_ms = now_ms + int((30 + TRIAL_PERIOD_DAYS) * 86_400_000)
        assert _approx(expected_ms)(result_ms)

    def test_non_trial_tariff_id_uses_regular_logic(self):
        """Любой tariff_id != 10 идёт по обычной логике max(expiry, now)."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        # Имитируем уже оплаченный ключ с tariff_id=7 и expiry в будущем.
        future_ms = now_ms + 10 * 86_400_000
        key = _key(
            tariff_id=7,
            created_at_ms=now_ms - 30 * 86_400_000,
            expiry_time_ms=future_ms,
        )
        result_ms = ExpiryCalculator().key_duration(key, days=30, number_of_months=1)
        # max(expiry, now) = expiry, +30.
        expected_ms = future_ms + int(30 * 86_400_000)
        assert _approx(expected_ms)(result_ms)
