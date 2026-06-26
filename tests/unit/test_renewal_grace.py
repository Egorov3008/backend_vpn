import pytest
from unittest.mock import AsyncMock, MagicMock

from services.core.keys.utils.renewal import KeyRenewal
from services.core.keys.utils.inbounds import GRACE_PERIOD_MS


def _key(status, expiry=2000, grace_expiry=9000, tariff_id=5, email="a@b.c"):
    k = MagicMock()
    k.expiry_time = expiry
    k.grace_expiry = grace_expiry
    k.tariff_id = tariff_id
    k.email = email
    k.client_id = "c1"
    k.tg_id = 1
    k.limit_ip = 3
    k.server_info = None
    k.created_at = 0
    return k


def _renewal(key_status_key, renew_from_grace_return=None):
    xui = MagicMock()
    xui.extend_client_key = AsyncMock(return_value=True)
    xui.set_inbounds = AsyncMock(return_value=True)
    md = MagicMock()
    md.keys.update = AsyncMock()
    refresh = MagicMock()
    refresh.refresh_key = MagicMock(side_effect=lambda k, *a, **kw: k)
    resetter = MagicMock()
    resetter.reset_key_after_renewal = AsyncMock()
    grace = MagicMock()
    grace.renew_from_grace = AsyncMock(return_value=renew_from_grace_return or _key("active"))
    kr = KeyRenewal(model_data=md, xui_session=xui, refresh_key=refresh, resetter=resetter,
                   grace_manager=grace)
    return kr, xui, md, refresh, resetter, grace


@pytest.mark.asyncio
async def test_active_renewal_sets_grace_expiry_and_reconciles():
    kr, xui, md, refresh, resetter, grace = _renewal(_key("active"))
    k = _key("active", expiry=10**13, grace_expiry=10**13 + 1)
    tariff = MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3)
    # refresh_key mutates expiry_time; simulate it
    refresh.refresh_key = MagicMock(side_effect=lambda key, *a, **kw: setattr(key, "expiry_time", 5000) or key)
    out = await kr.extension_key(k, conn=MagicMock(), server=MagicMock(), tariff=tariff, number_of_months=1)
    assert out.expiry_time == 5000
    assert out.grace_expiry == 5000 + GRACE_PERIOD_MS
    xui.set_inbounds.assert_awaited()  # reconcile to paid set
    xui.extend_client_key.assert_awaited_once()
    md.keys.update.assert_awaited_once()
    resetter.reset_key_after_renewal.assert_awaited_once()
    grace.renew_from_grace.assert_not_awaited()  # active path must not delegate


@pytest.mark.asyncio
async def test_grace_renewal_delegates_to_grace_manager():
    kr, xui, md, refresh, resetter, grace = _renewal(_key("grace"))
    # expiry in the past, grace_expiry far in the future => KeyStatus.of == GRACE
    k = _key("grace", expiry=1000, grace_expiry=10**13)
    tariff = MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3)
    out = await kr.extension_key(k, conn=MagicMock(), server=MagicMock(), tariff=tariff, number_of_months=1)
    grace.renew_from_grace.assert_awaited_once()
    xui.extend_client_key.assert_not_awaited()  # grace path does not use the normal extend
    md.keys.update.assert_not_awaited()  # grace path delegates DB write to grace_manager
    resetter.reset_key_after_renewal.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_renewal_raises():
    kr, xui, md, refresh, resetter, grace = _renewal(_key("expired"))
    k = _key("expired", expiry=2000, grace_expiry=2000)
    tariff = MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3)
    with pytest.raises(ValueError):
        await kr.extension_key(k, conn=MagicMock(), server=MagicMock(), tariff=tariff, number_of_months=1)
    # EXPIRED branch returns early — no panel/DB side effects, no delegation.
    xui.extend_client_key.assert_not_awaited()
    xui.set_inbounds.assert_not_awaited()
    md.keys.update.assert_not_awaited()
    grace.renew_from_grace.assert_not_awaited()
