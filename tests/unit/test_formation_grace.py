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

    return FormationKey(cache=cache, connected_data=connected_data, expiry=expiry), connected_data, expiry


@pytest.mark.asyncio
async def test_subscription_key_gets_paid_inbounds_and_grace():
    f, _, expiry = _formation()
    paid_tariff = MagicMock(id=5, amount=100.0, period=30, limit_ip=3)
    key = await f.form_new_key(tg_id=1, tariff=paid_tariff, server_id=1, number_of_months=1)
    assert key.inbound_ids == paid_inbound_ids()
    assert key.grace_expiry == 2000 + GRACE_PERIOD_MS
    expiry.key_duration_new_key.assert_called_once_with(30, 1)


@pytest.mark.asyncio
async def test_paid_tariff_period_normalized_to_30():
    """Regression: платный тариф с period=37 должен создавать ключ на 30 дней."""
    f, _, expiry = _formation()
    drift_tariff = MagicMock(id=5, amount=100.0, period=37, limit_ip=3, name_tariff="Pro-drift")
    key = await f.form_new_key(tg_id=1, tariff=drift_tariff, server_id=1, number_of_months=1)
    assert key is not None
    expiry.key_duration_new_key.assert_called_once_with(30, 1)


@pytest.mark.asyncio
async def test_trial_tariff_period_preserved():
    """Триальный тариф (id=10) не подвергается нормализации period."""
    f, _, expiry = _formation()
    trial_tariff = MagicMock(id=10, amount=0.0, period=7, limit_ip=1, name_tariff="trial")
    key = await f.form_new_key(tg_id=1, tariff=trial_tariff, server_id=1, number_of_months=1)
    assert key is not None
    expiry.key_duration_new_key.assert_called_once_with(7, 1)


@pytest.mark.asyncio
async def test_landing_override_key_has_no_grace():
    f, _, _ = _formation()
    # landing uses inbound_id_override and a free-ish tariff; is_subscription False
    landing_tariff = MagicMock(id=999, amount=0.0, period=1, limit_ip=1)
    key = await f.form_new_key(tg_id=-1, tariff=landing_tariff, server_id=1,
                               number_of_months=1, inbound_id_override=7)
    assert key.inbound_ids == [7]
    assert key.grace_expiry is None


@pytest.mark.asyncio
async def test_free_non_trial_key_has_no_grace():
    f, _, _ = _formation()
    free_tariff = MagicMock(id=2, amount=0.0, period=1, limit_ip=1)
    key = await f.form_new_key(tg_id=1, tariff=free_tariff, server_id=1, number_of_months=1)
    assert key.grace_expiry is None
    # Free non-subscription keys keep the server's offered inbounds (old behavior).
    assert key.inbound_ids == [11, 12]
