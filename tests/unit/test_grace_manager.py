import pytest
from unittest.mock import AsyncMock, MagicMock

from services.core.keys.utils.grace import GraceManager
from services.core.keys.utils.status import KeyStatus
from services.core.keys.utils.inbounds import paid_inbound_ids, GRACE_PERIOD_MS


def _key(expiry=2000, grace=9000, tg_id=1, email="a@b.c",
         inbound_ids=None, tariff_id=5, client_id="c1", converted_tg_id=None,
         landing_uid=None, grace_expiry=9000, **kw):
    k = MagicMock()
    k.expiry_time = expiry
    k.grace_expiry = grace_expiry
    k.tg_id = tg_id
    k.email = email
    k.inbound_ids = inbound_ids
    k.tariff_id = tariff_id
    k.client_id = client_id
    k.converted_tg_id = converted_tg_id
    k.landing_uid = landing_uid
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
    expiry.key_duration_new_key = MagicMock(return_value=5000)
    pool = MagicMock()
    return (GraceManager(xui, model_data, cache, expiry, pool),
            xui, model_data, cache, expiry)


@pytest.mark.asyncio
async def test_enter_grace_detaches_to_baseline():
    mgr, xui, md, cache, _ = _mgr()
    ok = await mgr.enter_grace(_key())
    assert ok is True
    args, _ = xui.set_inbounds.call_args
    assert args == ("a@b.c", [7]) or args[0] == "a@b.c" and args[1] == [7]
    cache.keys.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_expire_after_grace_detaches_all_and_deletes():
    mgr, xui, md, cache, _ = _mgr()
    ok = await mgr.expire_after_grace(_key())
    assert ok is True
    # last set_inbounds call must be empty set
    assert xui.set_inbounds.call_args.args[1] == []
    xui.delete_client.assert_awaited_once()
    cache.keys.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_expire_after_grace_tolerates_missing_client():
    mgr, xui, md, cache, _ = _mgr()
    xui.delete_client = AsyncMock(side_effect=Exception("not found"))
    ok = await mgr.expire_after_grace(_key())
    assert ok is True  # 404 treated as already-deleted
    cache.keys.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_expire_after_grace_real_failure_returns_false_and_keeps_cache():
    mgr, xui, md, cache, _ = _mgr()
    xui.delete_client = AsyncMock(side_effect=Exception("panel returned 503"))
    ok = await mgr.expire_after_grace(_key())
    assert ok is False  # real panel failure is NOT swallowed as success
    cache.keys.delete.assert_not_called()  # leave entry for retry


@pytest.mark.asyncio
async def test_renew_from_grace_reattaches_and_sets_expiry_grace():
    mgr, xui, md, cache, expiry = _mgr()
    k = _key(expiry=2000, grace_expiry=9000, tariff_id=5)
    out = await mgr.renew_from_grace(k, MagicMock(id=5, period=30, amount=100.0,
                                                  name_tariff="m", limit_ip=3), 1)
    assert out is not None
    assert out.expiry_time == 5000          # new expiry (from mocked calc)
    assert out.grace_expiry == 5000 + GRACE_PERIOD_MS
    # panel inbounds converged to paid set, panel expiryTime set via extend_client_key
    sets = [c.args[1] for c in xui.set_inbounds.call_args_list]
    assert paid_inbound_ids() in sets
    xui.extend_client_key.assert_awaited_once()
    md.keys.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_upgrade_from_landing_transfers_tg_and_reattaches():
    mgr, xui, md, cache, expiry = _mgr()
    k = _key(inbound_ids=[7], converted_tg_id=42, landing_uid="abc",
             grace_expiry=None, tg_id=-1)
    out = await mgr.upgrade_from_landing(k, MagicMock(id=10, period=7, amount=0.0,
                                                       name_tariff="trial", limit_ip=1), 1)
    assert out is not None
    assert out.tg_id == 42
    assert out.converted_tg_id == 42
    sets = [c.args[1] for c in xui.set_inbounds.call_args_list]
    assert paid_inbound_ids() in sets or sets[-1] == paid_inbound_ids()
    xui.extend_client_key.assert_awaited_once()
    md.keys.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_converges_to_active():
    mgr, xui, md, cache, _ = _mgr()
    k = _key(expiry=10**13, grace_expiry=10**13 + 1)  # far future => active
    await mgr.reconcile(k)
    target = xui.set_inbounds.call_args.args[1]
    assert target == paid_inbound_ids()
