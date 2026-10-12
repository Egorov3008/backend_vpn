"""Продление: сбой guard или панели не должен менять кеш и не должен записывать ключ в БД."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from client import XUIAPIError
from models import Key
from services.core.keys.utils.calculator import ExpiryCalculator
from services.core.keys.utils.renewal import KeyRenewal
from services.core.keys.utils.updating import KeyUpdater
from services.system.maintenance import maintenance_mode


@pytest.fixture(autouse=True)
def _maintenance_mode_off(monkeypatch):
    monkeypatch.setattr(maintenance_mode, "is_enabled", AsyncMock(return_value=False))


def _key(expiry_time):
    return Key(
        tg_id=1,
        client_id="c",
        email="user@example.com",
        expiry_time=expiry_time,
        key="sub",
        inbound_id=1,
        tariff_id=7,
    )


def _tariff():
    tariff = MagicMock()
    tariff.id = 7
    tariff.period = 30
    tariff.limit_ip = 1
    tariff.name_tariff = "Basic"
    return tariff


def _renewal(xui_result=True):
    xui = MagicMock()
    xui.extend_client_key = AsyncMock(return_value=xui_result)
    md = MagicMock()
    md.keys.update = AsyncMock()
    refresh = KeyUpdater(ExpiryCalculator())
    kr = KeyRenewal(model_data=md, xui_session=xui, refresh_key=refresh)
    return kr, xui, md


@pytest.mark.asyncio
async def test_guard_failure_leaves_cached_key_untouched():
    """Guard падает → объект из кеша сохраняет исходный expiry, повтор не добавит период дважды."""
    kr, xui, md = _renewal()
    cached = _key(expiry_time=1_000_000)
    md.keys.get_data = AsyncMock(return_value=cached)
    original_expiry = cached.expiry_time

    async def failing_guard(target):
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await kr.extension_key(
            cached, conn=MagicMock(), server=MagicMock(), tariff=_tariff(),
            number_of_months=1, expiry_guard=failing_guard,
        )

    assert cached.expiry_time == original_expiry
    xui.extend_client_key.assert_not_awaited()
    md.keys.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_panel_false_raises_and_does_not_write_db():
    """extend_client_key вернул False → ошибка, БД и кеш не обновляются."""
    kr, _xui, md = _renewal(xui_result=False)
    cached = _key(expiry_time=1_000_000)
    md.keys.get_data = AsyncMock(return_value=cached)
    original_expiry = cached.expiry_time

    with pytest.raises(XUIAPIError):
        await kr.extension_key(
            cached, conn=MagicMock(), server=MagicMock(), tariff=_tariff(), number_of_months=1,
        )

    assert cached.expiry_time == original_expiry
    md.keys.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_writes_renewed_copy_to_db():
    kr, xui, md = _renewal()
    cached = _key(expiry_time=1_000_000)
    md.keys.get_data = AsyncMock(return_value=cached)
    original_expiry = cached.expiry_time

    out = await kr.extension_key(
        cached, conn=MagicMock(), server=MagicMock(), tariff=_tariff(), number_of_months=1,
    )

    assert out.expiry_time > original_expiry
    assert cached.expiry_time == original_expiry
    sent = xui.extend_client_key.await_args.args[0]
    assert sent.expiry_time == out.expiry_time
    md.keys.update.assert_awaited_once()
    assert md.keys.update.await_args.args[1] is out


@pytest.mark.asyncio
async def test_overlapping_renewals_of_one_key_match_sequential_result():
    """Два параллельных продления одного ключа добавляют два периода, как при последовательных."""
    import asyncio
    from datetime import datetime

    base = int(datetime.now().timestamp() * 1000) + 10 * 86_400_000

    def make_env():
        store = {"user@example.com": _key(expiry_time=base)}
        snapshot = store["user@example.com"]

        async def slow_extend(_key_arg):
            await asyncio.sleep(0.01)
            return True

        async def get_data(email, conn=None):
            return store[email]

        async def update(conn, key_arg, search_data):
            store[key_arg.email] = key_arg

        xui = MagicMock()
        xui.extend_client_key = AsyncMock(side_effect=slow_extend)
        md = MagicMock()
        md.keys.get_data = AsyncMock(side_effect=get_data)
        md.keys.update = AsyncMock(side_effect=update)
        kr = KeyRenewal(model_data=md, xui_session=xui, refresh_key=KeyUpdater(ExpiryCalculator()))
        return kr, snapshot, store

    kr, snapshot, store = make_env()
    await kr.extension_key(snapshot, conn=MagicMock(), server=MagicMock(), tariff=_tariff(), number_of_months=1)
    await kr.extension_key(snapshot, conn=MagicMock(), server=MagicMock(), tariff=_tariff(), number_of_months=1)
    sequential_final = store["user@example.com"].expiry_time

    kr, snapshot, store = make_env()
    await asyncio.gather(
        kr.extension_key(snapshot, conn=MagicMock(), server=MagicMock(), tariff=_tariff(), number_of_months=1),
        kr.extension_key(snapshot, conn=MagicMock(), server=MagicMock(), tariff=_tariff(), number_of_months=1),
    )
    concurrent_final = store["user@example.com"].expiry_time

    assert concurrent_final == sequential_final
    assert concurrent_final > base + 30 * 86_400_000 * 1  # два периода, а не один
