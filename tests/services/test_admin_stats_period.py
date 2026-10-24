"""MonthPeriod: границы месяца в поясе админа и честное сравнение с прошлым месяцем."""
from datetime import date, datetime, timedelta, timezone

import pytest

from services.admin_stats.period import InvalidPeriod, MonthPeriod


def test_current_month_compares_with_same_days_of_previous_month():
    now = datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc)

    period = MonthPeriod.build(None, "Europe/Moscow", now=now)

    assert period.first_day == date(2026, 10, 1)
    assert period.prev_first_day == date(2026, 9, 1)
    assert period.days_elapsed == 24
    assert period.days_in_month == 31
    assert period.prev_cutoff - period.prev_start == period.cutoff - period.start


def test_day_after_short_month_never_runs_past_its_end():
    # 31 марта против февраля: в феврале 28 дней, отрезок упирается в конец февраля.
    now = datetime(2027, 3, 31, 12, 0, tzinfo=timezone.utc)

    period = MonthPeriod.build(None, "UTC", now=now)

    assert period.prev_cutoff == period.start
    assert period.prev_days_in_month == 28


def test_past_month_is_whole():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)

    period = MonthPeriod.build("2026-01", "UTC", now=now)

    assert period.cutoff == period.end
    assert period.days_elapsed == 31
    assert period.prev_first_day == date(2025, 12, 1)
    assert period.end - period.start == timedelta(days=31)


def test_month_boundary_in_admin_timezone():
    # 22:30 UTC 31 октября — уже 1 ноября по Москве.
    now = datetime(2026, 10, 31, 22, 30, tzinfo=timezone.utc)

    period = MonthPeriod.build(None, "Europe/Moscow", now=now)

    assert period.first_day == date(2026, 11, 1)
    assert period.days_elapsed == 1


@pytest.mark.parametrize("month,tz", [("2026-13", "UTC"), ("2026-1", "UTC"), ("2027-01", "UTC"), (None, "Nowhere/City"), (None, "")])
def test_invalid_period(month, tz):
    with pytest.raises(InvalidPeriod):
        MonthPeriod.build(month, tz, now=datetime(2026, 10, 24, tzinfo=timezone.utc))
