from datetime import datetime
from typing import Tuple

from models import Key
from models import Tariff


class KeyModel:
    """Модель ключа — инкапсулирует данные и бизнес-логику."""

    def __init__(self, key: Key, tariff: Tariff):
        self.key = key
        self.tariff = tariff

    @property
    def is_expired(self) -> bool:
        expiry_dt = datetime.utcfromtimestamp(self.key.expiry_time / 1000)
        return expiry_dt <= datetime.utcnow()

    @property
    def time_left(self):
        expiry_dt = datetime.utcfromtimestamp(self.key.expiry_time / 1000)
        return expiry_dt - datetime.utcnow()

    @property
    def days_left(self) -> int:
        return max(0, self.time_left.days)

    @property
    def hours_left(self) -> int:
        return self.time_left.seconds // 3600 if self.time_left.days == 0 else 0

    @property
    def status(self) -> Tuple[str, str]:
        """Возвращает (эмодзи, текст статуса)"""
        if self.is_expired:
            return "🔴", "Истекла"
        elif self.time_left.days > 0:
            return "🟢", "Активна"
        else:
            return "🟡", "Заканчивается"

    @property
    def formatted_expiry_date(self) -> str:
        expiry_dt = datetime.utcfromtimestamp(self.key.expiry_time / 1000)
        return expiry_dt.strftime("%d %B %Y года")

    @property
    def used_traffic_gb(self) -> float:
        return round(self.key.used_traffic / (1024**3), 2)

    @property
    def time_left_message(self) -> str:
        if self.is_expired:
            return "Осталось часов: 0"
        elif self.time_left.days > 0:
            return f"Осталось дней: {self.days_left}"
        else:
            return f"Осталось часов: {self.hours_left}"

    def to_dict(self) -> dict:
        """Готовый словарь для передачи в aiogram_dialog"""
        emoji, status_text = self.status
        tariff_name = self.tariff.name_tariff if self.tariff else "Не указан"

        return {
            "error": False,
            "keys": self.key.key,
            "tariff_name": tariff_name,
            "used_traffic": self.used_traffic_gb,
            "expiry_date": self.formatted_expiry_date,
            "status_emoji": emoji,
            "status_text": status_text,
            "time_left_message": self.time_left_message,
            "is_trial": self.key.tariff_id == 10,
            "not_trial_tariff": self.key.tariff_id != 10,
            "is_active": not self.is_expired,
            "days_left": self.days_left,
            "hours_left": self.hours_left,
        }
