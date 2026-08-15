"""Tests: TrafficUpdater.update_key_with_traffic — panel is now the source
of truth for expiry_time (and limit_ip), matching the direction already
used by KeyCreator for restored keys. expiry_time == 0 from the panel is a
valid value (unlimited/no-expiry tariff) and must be written through as-is.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.synchron.traffic import TrafficUpdater


def _client(email="user@example.com", expiry_time=1_800_000_000_000, limit_ip=3):
    c = MagicMock()
    c.email = email
    c.expiry_time = expiry_time
    c.limit_ip = limit_ip
    return c


def _key(email="user@example.com", expiry_time=1000, limit_ip=1):
    k = MagicMock()
    k.email = email
    k.expiry_time = expiry_time
    k.limit_ip = limit_ip
    return k


def _traffic_data():
    return {"headers": {"Subscription-Userinfo": "upload=100; download=200"}}


@pytest.fixture
def updater():
    model_data = MagicMock()
    model_data.keys = MagicMock()
    model_data.keys.update = AsyncMock()
    return TrafficUpdater(model_data=model_data)


@pytest.mark.asyncio
async def test_panel_expiry_overwrites_db_expiry(updater):
    key = _key(expiry_time=1_700_000_000_000)  # устаревшее значение в БД
    client = _client(expiry_time=1_800_000_000_000)  # свежее значение с панели

    result = await updater.update_key_with_traffic(
        pool=MagicMock(), key=key, client=client, traffic_data=_traffic_data()
    )

    assert result is True
    assert key.expiry_time == 1_800_000_000_000


@pytest.mark.asyncio
async def test_panel_expiry_zero_is_written_as_unlimited_not_skipped(updater):
    """Regression: expiry_time=0 с панели — валидное значение ("без срока
    действия"), а не признак бага/потери данных, и должно записываться в БД,
    а не пропускаться."""
    key = _key(expiry_time=1_800_000_000_000)  # в БД ранее был реальный срок
    client = _client(expiry_time=0)  # панель говорит "без срока действия"

    result = await updater.update_key_with_traffic(
        pool=MagicMock(), key=key, client=client, traffic_data=_traffic_data()
    )

    assert result is True
    assert key.expiry_time == 0


@pytest.mark.asyncio
async def test_limit_ip_still_synced_from_panel(updater):
    key = _key(limit_ip=1)
    client = _client(limit_ip=5)

    await updater.update_key_with_traffic(
        pool=MagicMock(), key=key, client=client, traffic_data=_traffic_data()
    )

    assert key.limit_ip == 5


@pytest.mark.asyncio
async def test_no_traffic_data_skips_update(updater):
    key = _key(expiry_time=1_700_000_000_000)
    client = _client(expiry_time=1_800_000_000_000)

    result = await updater.update_key_with_traffic(
        pool=MagicMock(), key=key, client=client, traffic_data=None
    )

    assert result is False
    assert key.expiry_time == 1_700_000_000_000
    updater.model_data.keys.update.assert_not_awaited()
