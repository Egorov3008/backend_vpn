"""Ежедневная сводка успешных платежей: выборка, формат и рассылка админам."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from background.scheduler import SyncScheduler
from services.core.payment.daily_report import (
    get_daily_report,
)

MSK = ZoneInfo("Europe/Moscow")


def _pool_with_rows(rows):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


@pytest.mark.asyncio
async def test_get_daily_report_aggregates_by_operation_and_passes_window():
    rows = [
        {"operation": "create_key", "count": 2, "total": 300.0},
        {"operation": "renew_key", "count": 1, "total": 150.0},
    ]
    pool, conn = _pool_with_rows(rows)
    end = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
    start = end - timedelta(hours=24)

    report = await get_daily_report(pool, start, end)

    assert report.count == 3
    assert report.total == pytest.approx(450.0)
    assert report.avg == pytest.approx(150.0)
    assert [op.operation for op in report.by_operation] == ["create_key", "renew_key"]
    # Окно передаётся параметрами: [start, end)
    args = conn.fetch.call_args.args
    assert args[1] == start
    assert args[2] == end
    assert "succeeded_at >= $1" in args[0]
    assert "succeeded_at < $2" in args[0]


@pytest.mark.asyncio
async def test_get_daily_report_empty_day_is_zero():
    pool, _ = _pool_with_rows([])
    end = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)

    report = await get_daily_report(pool, end - timedelta(hours=24), end)

    assert report.count == 0
    assert report.total == 0.0
    assert report.avg == 0.0
    assert report.by_operation == []


def _scheduler_with_pool(pool):
    service_data = MagicMock()
    return SyncScheduler(service_data=service_data, pool=pool)


def _settings(**overrides):
    base = {
        "daily_payments_report_enabled": True,
        "daily_payments_report_tz": "Europe/Moscow",
        "bot_token": "token",
        "support_chat_url": "",
        **overrides,
    }
    return MagicMock(**base)


@pytest.mark.asyncio
async def test_report_sent_to_each_admin_and_zero_placeholder_skipped():
    pool, _ = _pool_with_rows([])
    scheduler = _scheduler_with_pool(pool)
    notifier = MagicMock()
    notifier.send_message = AsyncMock()

    with patch("config.settings", _settings()), \
         patch("config.ADMIN_ID", [0, 111, 222]), \
         patch("services.core.notifications.TelegramBotNotifier", return_value=notifier):
        await scheduler.run_daily_payments_report()

    sent_to = [c.args[0] for c in notifier.send_message.await_args_list]
    assert sent_to == [111, 222]


@pytest.mark.asyncio
async def test_report_skipped_when_disabled():
    pool, conn = _pool_with_rows([])
    scheduler = _scheduler_with_pool(pool)
    notifier = MagicMock()
    notifier.send_message = AsyncMock()

    with patch("config.settings", _settings(daily_payments_report_enabled=False)), \
         patch("config.ADMIN_ID", [111]), \
         patch("services.core.notifications.TelegramBotNotifier", return_value=notifier):
        await scheduler.run_daily_payments_report()

    conn.fetch.assert_not_called()
    notifier.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_one_failing_admin_does_not_block_others():
    pool, _ = _pool_with_rows([])
    scheduler = _scheduler_with_pool(pool)
    notifier = MagicMock()
    notifier.send_message = AsyncMock(side_effect=[RuntimeError("boom"), None])

    with patch("config.settings", _settings()), \
         patch("config.ADMIN_ID", [111, 222]), \
         patch("services.core.notifications.TelegramBotNotifier", return_value=notifier):
        await scheduler.run_daily_payments_report()

    assert notifier.send_message.await_count == 2


def test_daily_job_registered_at_configured_hour():
    from background.scheduler import create_scheduler

    with patch("config.settings", _settings(daily_payments_report_hour=18)):
        scheduler = create_scheduler(service_data=MagicMock(), pool=MagicMock())

    job = scheduler.get_job("daily_payments_report")
    assert job is not None
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "18"
    assert fields["minute"] == "0"
