import pytest
from unittest.mock import AsyncMock, MagicMock

from services.core.keys.utils.landing_upgrade import upgrade_landing_key
from services.core.keys.utils.inbounds import paid_inbound_ids


def _key(expiry_time=1000, converted_tg_id=None, tg_id=-1):
    k = MagicMock()
    k.email = "a@b.c"
    k.key = "https://sub.example/a@b.c"
    k.expiry_time = expiry_time
    k.converted_tg_id = converted_tg_id
    k.tg_id = tg_id
    k.inbound_ids = [7]
    k.client_id = "c1"
    k.limit_ip = 1
    return k


def _tariff():
    return MagicMock(id=10, period=7, amount=0.0, name_tariff="trial", limit_ip=1)


@pytest.mark.asyncio
async def test_upgrade_success_updates_key_and_persists():
    key = _key(converted_tg_id=42, tg_id=-1)
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=True)
    xui.extend_client_key = AsyncMock(return_value=True)

    model_data = MagicMock()
    model_data.keys.update = AsyncMock()
    cache = MagicMock()
    cache.keys.set = AsyncMock()
    pool = MagicMock()
    tariff = _tariff()

    result = await upgrade_landing_key(
        xui_session=xui,
        model_data=model_data,
        cache=cache,
        pool=pool,
        key=key,
        tariff=tariff,
        new_expiry=8000,
        transfer_tg=True,
    )

    assert result is key
    xui.set_inbounds.assert_awaited_once_with(key.email, paid_inbound_ids())
    xui.extend_client_key.assert_awaited_once()
    assert key.expiry_time == 8000
    assert key.tariff_id == tariff.id
    assert key.name_tariff == tariff.name_tariff
    assert key.period == tariff.period
    assert key.amount == tariff.amount
    assert key.limit_ip == tariff.limit_ip
    assert key.notified_24h is False
    assert key.notified_10h is False
    assert key.notified_expired_grace is False
    # transfer_tg=True and converted_tg_id is truthy → tg_id transferred
    assert key.tg_id == 42
    model_data.keys.update.assert_awaited_once_with(pool, key, search_data={"email": key.email})
    cache.keys.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_upgrade_no_transfer_tg_keeps_original_tg_id():
    key = _key(converted_tg_id=42, tg_id=-1)
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=True)
    xui.extend_client_key = AsyncMock(return_value=True)

    model_data = MagicMock()
    model_data.keys.update = AsyncMock()
    cache = MagicMock()
    cache.keys.set = AsyncMock()

    result = await upgrade_landing_key(
        xui_session=xui,
        model_data=model_data,
        cache=cache,
        pool=MagicMock(),
        key=key,
        tariff=_tariff(),
        new_expiry=8000,
        transfer_tg=False,
    )

    assert result is key
    assert key.tg_id == -1


@pytest.mark.asyncio
async def test_upgrade_set_inbounds_failure_returns_none_and_preserves_expiry():
    key = _key(expiry_time=1000, converted_tg_id=42)
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=False)
    xui.extend_client_key = AsyncMock(return_value=True)

    model_data = MagicMock()
    model_data.keys.update = AsyncMock()
    cache = MagicMock()
    cache.keys.set = AsyncMock()

    result = await upgrade_landing_key(
        xui_session=xui,
        model_data=model_data,
        cache=cache,
        pool=MagicMock(),
        key=key,
        tariff=_tariff(),
        new_expiry=8000,
        transfer_tg=True,
    )

    assert result is None
    xui.extend_client_key.assert_not_awaited()
    model_data.keys.update.assert_not_awaited()
    cache.keys.set.assert_not_awaited()
    # expiry_time never touched since we failed before setting it
    assert key.expiry_time == 1000


@pytest.mark.asyncio
async def test_upgrade_extend_client_key_failure_restores_expiry():
    key = _key(expiry_time=1000, converted_tg_id=42)
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=True)
    xui.extend_client_key = AsyncMock(return_value=False)

    model_data = MagicMock()
    model_data.keys.update = AsyncMock()
    cache = MagicMock()
    cache.keys.set = AsyncMock()

    result = await upgrade_landing_key(
        xui_session=xui,
        model_data=model_data,
        cache=cache,
        pool=MagicMock(),
        key=key,
        tariff=_tariff(),
        new_expiry=8000,
        transfer_tg=True,
    )

    assert result is None
    # expiry_time restored to the pre-upgrade value on failure
    assert key.expiry_time == 1000
    model_data.keys.update.assert_not_awaited()
    cache.keys.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_upgrade_no_converted_tg_id_does_not_transfer():
    key = _key(converted_tg_id=None, tg_id=-1)
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=True)
    xui.extend_client_key = AsyncMock(return_value=True)

    model_data = MagicMock()
    model_data.keys.update = AsyncMock()
    cache = MagicMock()
    cache.keys.set = AsyncMock()

    result = await upgrade_landing_key(
        xui_session=xui,
        model_data=model_data,
        cache=cache,
        pool=MagicMock(),
        key=key,
        tariff=_tariff(),
        new_expiry=8000,
        transfer_tg=True,
    )

    assert result is key
    assert key.tg_id == -1
