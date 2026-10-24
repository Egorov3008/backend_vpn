"""Период статистики: календарный месяц в часовом поясе админа и такой же отрезок прошлого месяца."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


class InvalidPeriod(ValueError):
    """Некорректный месяц или часовой пояс."""


def _shift_month(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True)
class MonthPeriod:
    """Месяц ``[start, end)`` и отрезок прошлого месяца той же длины для сравнения.

    ``cutoff`` — «по сегодня»: для текущего месяца это сейчас, для прошедшего — его конец.
    Сравнение честное: 1–24 октября против 1–24 сентября (``prev_start``..``prev_cutoff``),
    а весь прошлый месяц — ``prev_start``..``start``. Границы суток — в поясе ``tz``.
    """

    tz: str
    first_day: date
    start: datetime
    end: datetime
    cutoff: datetime
    prev_first_day: date
    prev_start: datetime
    prev_cutoff: datetime

    @classmethod
    def build(cls, month: str | None, tz: str, now: datetime | None = None) -> "MonthPeriod":
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise InvalidPeriod(f"Неизвестный часовой пояс: {tz}") from e

        local_now = (now or datetime.now(zone)).astimezone(zone)
        this_month = local_now.date().replace(day=1)
        if month:
            m = _MONTH_RE.match(month)
            if not m or not 1 <= int(m.group(2)) <= 12:
                raise InvalidPeriod("month должен быть в формате ГГГГ-ММ")
            first_day = date(int(m.group(1)), int(m.group(2)), 1)
            if first_day > this_month:
                raise InvalidPeriod("Месяц ещё не начался")
        else:
            first_day = this_month

        def midnight(d: date) -> datetime:
            return datetime.combine(d, time.min, tzinfo=zone)

        start = midnight(first_day)
        end = midnight(_shift_month(first_day, 1))
        cutoff = min(local_now, end)
        prev_first_day = _shift_month(first_day, -1)
        prev_start = midnight(prev_first_day)
        prev_cutoff = min(prev_start + (cutoff - start), start)
        return cls(
            tz=tz,
            first_day=first_day,
            start=start,
            end=end,
            cutoff=cutoff,
            prev_first_day=prev_first_day,
            prev_start=prev_start,
            prev_cutoff=prev_cutoff,
        )

    @property
    def days_in_month(self) -> int:
        return (self.end - self.start).days

    @property
    def days_elapsed(self) -> int:
        """Сколько дней месяца уже наступило (сегодня считается)."""
        if self.cutoff >= self.end:
            return self.days_in_month
        return self.cutoff.astimezone(self.start.tzinfo).day

    @property
    def prev_days_in_month(self) -> int:
        return (self.start - self.prev_start).days
