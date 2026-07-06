"""Тесты записи продлений из grace в grace_renewal_log."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.core.keys.utils.grace import GraceManager
from services.core.keys.utils.inbounds import GRACE_PERIOD_MS


def _key(expiry=2000, grace_expiry=9000, tg_id=1, email="a@b.c",
         tariff_id=5, client_id="c1", **kw):
    k = MagicMock()
    k.expiry_time = expiry
    k.grace_expiry = grace_expiry
    k.tg_id = tg_id
    k.email = email
    k.inbound_ids = None
    k.tariff_id = tariff_id
    k.client_id = client_id
    k.converted_tg_id = None
    k.landing_uid = None
    k.limit_ip = 3
    k.name_tariff = "t"
    k.period = 30
    k.amount = 100.0
    k.notified_24h = False
    k.notified_10h = False
    k.notified_expired_grace = False
    return k


def _mgr():
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=True)
    xui.extend_client_key = AsyncMock(return_value=True)
    xui.delete_client = AsyncMock(return_value=True)
    model_data = MagicMock()
    model_data.keys.update = AsyncMock()
    cache = MagicMock()
    cache.keys.set = AsyncMock()
    cache.keys.delete = AsyncMock()
    expiry = MagicMock()
    expiry.key_duration = MagicMock(return_value=5000)
    conn = AsyncMock()
    conn.execute = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=cm)
    mgr = GraceManager(xui, model_data, cache, expiry, pool)
    return mgr, pool, conn


@pytest.mark.asyncio
async def test_renew_from_grace_writes_log_row():
    mgr, pool, conn = _mgr()
    k = _key(expiry=2000, grace_expiry=9000, tariff_id=5)
    tariff = MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3)
    out = await mgr.renew_from_grace(k, tariff, 1)
    assert out is not None
    conn.execute.assert_awaited_once()
    args, _ = conn.execute.call_args
    assert args[0] == "INSERT INTO grace_renewal_log (email, tg_id) VALUES ($1, $2)"
    assert args[1] == k.email
    assert args[2] == k.tg_id


@pytest.mark.asyncio
async def test_renew_from_grace_no_log_on_apply_paid_failure():
    """Если _apply_paid падает (extend_client_key=False), лог не пишем."""
    mgr, pool, conn = _mgr()
    mgr.xui.extend_client_key = AsyncMock(return_value=False)
    k = _key(expiry=2000, grace_expiry=9000, tariff_id=5)
    tariff = MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3)
    out = await mgr.renew_from_grace(k, tariff, 1)
    assert out is None
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_log_renewal_failure_does_not_break_renewal():
    """Сбой INSERT в grace_renewal_log не должен рвать продление."""
    mgr, pool, conn = _mgr()
    conn.execute = AsyncMock(side_effect=Exception("db down"))
    k = _key(expiry=2000, grace_expiry=9000, tariff_id=5)
    tariff = MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3)
    out = await mgr.renew_from_grace(k, tariff, 1)
    assert out is not None  # продление прошло несмотря на сбой лога
    assert out.expiry_time == 5000
    assert out.grace_expiry == 5000 + GRACE_PERIOD_MS


@pytest.mark.asyncio
async def test_upgrade_from_landing_does_not_write_log():
    """upgrade_from_landing — это не grace->paid, лог не пишем."""
    mgr, pool, conn = _mgr()
    k = _key(inbound_ids=[7], converted_tg_id=42, landing_uid="abc",
             grace_expiry=None, tg_id=-1)
    tariff = MagicMock(id=10, period=7, amount=0.0, name_tariff="trial", limit_ip=1)
    out = await mgr.upgrade_from_landing(k, tariff, 1)
    assert out is not None
    conn.execute.assert_not_awaited()