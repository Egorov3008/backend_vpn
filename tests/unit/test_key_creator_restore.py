"""Tests: restoring a lost DB row from panel data.

KeyCreator.create_key (restore-from-panel branch) now sets expiry_time
directly from the panel client's expiry_time — the grace model
(grace_expiry / GRACE_PERIOD_MS pre-set invariant) was removed entirely.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.synchron.key_creator import KeyCreator


def _client(email="user@example.com", expiry_time=1_800_000_000_000, limit_ip=3,
            tg_id=42, sub_id="user@example.com", inbound_id=7, client_id="cid-1"):
    c = MagicMock()
    c.email = email
    c.expiry_time = expiry_time
    c.limit_ip = limit_ip
    c.tg_id = tg_id
    c.sub_id = sub_id
    c.inbound_id = inbound_id
    c.id = client_id
    return c


def _server():
    s = MagicMock()
    s.subscription_url = "https://sub.example.com"
    return s


@pytest.fixture
def creator():
    model_data = MagicMock()
    model_data.servers.get_data = AsyncMock(return_value=_server())
    model_data.keys.save_data = AsyncMock()
    tariff_matcher = MagicMock()
    tariff_matcher.match = AsyncMock(return_value=20)
    return KeyCreator(model_data=model_data, pool=MagicMock(), tariff_matcher=tariff_matcher)


@pytest.mark.asyncio
async def test_restore_uses_panel_expiry_directly_for_subscription(creator):
    """Subscription tariff (amount>0): expiry_time is taken verbatim from the
    panel client — no grace arithmetic."""
    creator.model_data.tariffs.get_data = AsyncMock(
        return_value=MagicMock(id=20, amount=500.0)
    )
    client = _client(expiry_time=1_800_000_000_000)

    key = await creator.create_key(client)

    assert key is not None
    assert key.expiry_time == 1_800_000_000_000


@pytest.mark.asyncio
async def test_restore_uses_panel_expiry_directly_for_free_key(creator):
    """Free tariff (amount=0): expiry_time is taken verbatim from the panel
    client as well — same unconditional path."""
    creator.model_data.tariffs.get_data = AsyncMock(
        return_value=MagicMock(id=20, amount=0.0)
    )
    client = _client(expiry_time=1_800_000_000_000)

    key = await creator.create_key(client)

    assert key is not None
    assert key.expiry_time == 1_800_000_000_000


@pytest.mark.asyncio
async def test_restore_unknown_tariff_still_uses_panel_expiry(creator):
    """If the tariff can't be resolved, tariff_id ends up None but
    expiry_time is still taken directly from the panel client."""
    creator.model_data.tariffs.get_data = AsyncMock(return_value=None)
    client = _client(expiry_time=1_800_000_000_000)

    key = await creator.create_key(client)

    assert key is not None
    assert key.expiry_time == 1_800_000_000_000


@pytest.mark.asyncio
async def test_restore_zero_panel_expiry_kept_as_is(creator):
    """panel expiryTime==0 (3x-ui's "no expiry" convention) is stored as-is —
    no grace subtraction that could push it negative."""
    creator.model_data.tariffs.get_data = AsyncMock(
        return_value=MagicMock(id=20, amount=100.0)
    )
    client = _client(expiry_time=0)

    key = await creator.create_key(client)

    assert key is not None
    assert key.expiry_time == 0
