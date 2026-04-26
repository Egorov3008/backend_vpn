from datetime import datetime, timedelta

from models import Key


class ExpiryCalculator:
    def key_duration_new_key(self, days: int, number_of_months: int = 1):
        """Вычисляет новое время expiry для нового ключа"""
        days = days * number_of_months
        expiry_time = datetime.now() + timedelta(days=days)
        return int(expiry_time.timestamp() * 1000)

    def key_duration(self, key_details: Key, days: int, number_of_months: int = 1) -> int:
        """Вычисляет новое время expiry для существующего ключа"""
        days *= int(number_of_months)
        current_time_ms = int(datetime.now().timestamp() * 1000)
        expiry_time = max(key_details.expiry_time, current_time_ms)
        expiry_datetime = datetime.fromtimestamp(expiry_time / 1000)
        new_expiry_datetime = expiry_datetime + timedelta(days=days)
        new_expiry_timestamp = int(new_expiry_datetime.timestamp() * 1000)

        return new_expiry_timestamp
