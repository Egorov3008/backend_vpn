from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from models import Key


@dataclass
class TimeHelper:
    """
    Хелпер для работы со временем в миллисекундах.

    Предоставляет свойства с временными метками в миллисекундах
    для различных временных интервалов относительно текущего момента.
    Все значения вычисляются при каждом обращении (не кэшируются).
    """

    @property
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    @property
    def now_ms(self) -> int:
        """
        Получить текущее время в миллисекундах.

        Returns:
            int: Текущая временная метка в миллисекундах (Unix timestamp * 1000)

        Example:
            >>> time_helper = TimeHelper()
            >>> current_time = time_helper.now_ms
            >>> print(f"Current time: {current_time} ms")
        """
        return int(self.now.timestamp() * 1000)

    @property
    def two_days_ago_ms(self) -> int:
        """
        Получить временную метку 48 часов назад в миллисекундах.

        Returns:
            int: Временная метка, соответствующая моменту 2 дня назад в миллисекундах

        Example:
            >>> time_helper = TimeHelper()
            >>> two_days_ago = time_helper.two_days_ago_ms
            >>> # Используется для проверки ключей, созданных более 2 дней назад
        """
        return int((self.now - timedelta(days=2)).timestamp() * 1000)

    @property
    def twenty_four_hours_ms(self) -> int:
        """
        Получить временную метку через 24 часа в миллисекундах.

        Returns:
            int: Временная метка, соответствующая моменту через 24 часа в миллисекундах

        Example:
            >>> time_helper = TimeHelper()
            >>> tomorrow = time_helper.twenty_four_hours_ms
            >>> # Используется для проверки ключей, истекающих в ближайшие 24 часа
        """
        return int((self.now + timedelta(hours=24)).timestamp() * 1000)

    @property
    def ten_hours_ms(self) -> int:
        """
        Получить временную метку через 10 часов в миллисекундах.

        Returns:
            int: Временная метка, соответствующая моменту через 10 часов в миллисекундах

        Example:
            >>> time_helper = TimeHelper()
            >>> in_ten_hours = time_helper.ten_hours_ms
            >>> # Используется для проверки ключей, истекающих в ближайшие 10 часов
        """
        return int((self.now + timedelta(hours=10)).timestamp() * 1000)

    @property
    def seventy_two_hours_ms(self) -> int:
        """
        Получить временную метку через 72 часа в миллисекундах.

        Returns:
            int: Временная метка, соответствующая моменту через 72 часа (3 дня) в миллисекундах

        Example:
            >>> time_helper = TimeHelper()
            >>> in_three_days = time_helper.seventy_two_hours_ms
            >>> # Используется для проверки ключей, истекающих в ближайшие 3 дня
        """
        return int((self.now + timedelta(hours=72)).timestamp() * 1000)


def valid_24h_keys(key: Key):
    """Валидация ключа по времени истечения"""
    time_helper = TimeHelper()
    twenty_four_hours_ms = time_helper.twenty_four_hours_ms
    now_ms = time_helper.now_ms
    expiry_time = key.expiry_time
    return twenty_four_hours_ms > expiry_time > now_ms
