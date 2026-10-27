"""Окно отчёта «Поведение»: последние N дней до текущего момента в часовом поясе админа."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_DAYS = 365


class InvalidWindow(ValueError):
    """Некорректное число дней или часовой пояс."""


@dataclass(frozen=True)
class BehaviorWindow:
    """``[start, end)``: ``end`` — сейчас, ``start`` — полночь (в ``tz``) дня, с которого
    отсчитаны ``days`` календарных дней, сегодня включительно."""

    tz: str
    days: int
    start: datetime
    end: datetime

    @classmethod
    def build(cls, days: int, tz: str, now: datetime | None = None) -> "BehaviorWindow":
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise InvalidWindow(f"Неизвестный часовой пояс: {tz}") from e
        if not 1 <= days <= MAX_DAYS:
            raise InvalidWindow(f"days должен быть от 1 до {MAX_DAYS}")
        end = (now or datetime.now(zone)).astimezone(zone)
        first_day = end.date() - timedelta(days=days - 1)
        start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=zone)
        return cls(tz=tz, days=days, start=start, end=end)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def day_list(self) -> list[date]:
        first = self.start.date()
        return [first + timedelta(days=i) for i in range(self.days)]

    def ms(self, moment: datetime) -> int:
        """keys.created_at и expiry_time хранятся в миллисекундах."""
        return int(moment.timestamp() * 1000)
