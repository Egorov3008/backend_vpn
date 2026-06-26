import pytest
from unittest.mock import AsyncMock, MagicMock

from services.core.keys.utils.formtion import FormationKey
from services.core.keys.utils.inbounds import paid_inbound_ids, GRACE_PERIOD_MS


def _formation(cache_all_keys=()):
    cache = MagicMock()
    cache.keys.all = AsyncMock(return_value=list(cache_all_keys))
    connected_data = MagicMock()
    connected_data.data = AsyncMock(return_value={
        "subscription_url": "https://sub.example",
        "inbound_ids": [11, 12],
    })
    expiry = MagicMock()
    expiry.key_duration_new_key = MagicMock(return_value=2000)
    return FormationKey(cache=cache, connected_data=connected_data, expiry=expiry), connected_data


@pytest.mark.asyncio
async def test_subscription_key_gets_paid_inbounds_and_grace():
    f, _ = _formation()
    paid_tariff = MagicMock(id=5, amount=100.0, period=30, limit_ip=3)
    key = await f.form_new_key(tg_id=1, tariff=paid_tariff, server_id=1, number_of_months=1)
    assert key.inbound_ids == paid_inbound_ids()
    assert key.grace_expiry == 2000 + GRACE_PERIOD_MS


@pytest.mark.asyncio
async def test_landing_override_key_has_no_grace():
    f, _ = _formation()
    # landing uses inbound_id_override and a free-ish tariff; is_subscription False
    landing_tariff = MagicMock(id=999, amount=0.0, period=1, limit_ip=1)
    key = await f.form_new_key(tg_id=-1, tariff=landing_tariff, server_id=1,
                               number_of_months=1, inbound_id_override=7)
    assert key.inbound_ids == [7]
    assert key.grace_expiry is None


@pytest.mark.asyncio
async def test_free_non_trial_key_has_no_grace():
    f, _ = _formation()
    free_tariff = MagicMock(id=2, amount=0.0, period=1, limit_ip=1)
    key = await f.form_new_key(tg_id=1, tariff=free_tariff, server_id=1, number_of_months=1)
    assert key.grace_expiry is None