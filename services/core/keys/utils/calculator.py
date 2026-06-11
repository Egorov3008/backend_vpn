from datetime import datetime, timedelta

from models import Key

# ID триального тарифа (используется в service.py / key_model.py для пометки is_trial).
TRIAL_TARIFF_ID: int = 10
# Длительность триала в днях (TRIAL_TIME из .env; жёстко зашито как фоллбэк).
TRIAL_PERIOD_DAYS: int = 7


class ExpiryCalculator:
    def key_duration_new_key(self, days: int, number_of_months: int = 1):
        """Вычисляет новое время expiry для нового ключа"""
        days = days * number_of_months
        expiry_time = datetime.now() + timedelta(days=days)
        return int(expiry_time.timestamp() * 1000)

    def key_duration(
        self,
        key_details: Key,
        days: int,
        number_of_months: int = 1,
    ) -> int:
        """Вычисляет новое время expiry для существующего ключа.

        Правила:
        - **Триальный ключ** (``key_details.tariff_id == TRIAL_TARIFF_ID``):
          от текущего момента прибавляем ``tariff.period * number_of_months``
          (полный оплаченный месяц) плюс неизрасходованный остаток триала
          (``TRIAL_PERIOD_DAYS - elapsed_since_creation``).
        - **Обычный ключ**: прибавляем ``days * number_of_months`` к ``max(now, expiry)``.
        """
        days *= int(number_of_months)

        current_time_ms = int(datetime.now().timestamp() * 1000)

        if key_details.tariff_id == TRIAL_TARIFF_ID:
            # Продление триала: месяц по тарифу + неиспользованный остаток trial.
            # Если ключ только что создан, остаток ≈ TRIAL_PERIOD_DAYS.
            created_ms = int(key_details.created_at or current_time_ms)
            elapsed_days = max(0.0, (current_time_ms - created_ms) / 86_400_000.0)
            remaining_trial_days = max(0, TRIAL_PERIOD_DAYS - elapsed_days)
            total_days = days + remaining_trial_days
            new_expiry_datetime = datetime.now() + timedelta(days=total_days)
            return int(new_expiry_datetime.timestamp() * 1000)

        # Стандартное продление: к max(now, expiry) прибавляем тарифный период.
        expiry_time = max(key_details.expiry_time, current_time_ms)
        expiry_datetime = datetime.fromtimestamp(expiry_time / 1000)
        new_expiry_datetime = expiry_datetime + timedelta(days=days)
        return int(new_expiry_datetime.timestamp() * 1000)
