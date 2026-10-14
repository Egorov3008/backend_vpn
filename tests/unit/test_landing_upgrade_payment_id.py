"""Апгрейд лендинг-ключа записывает payment_id, если апгрейд выполнен по платежу."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.core.keys.utils.landing_upgrade import upgrade_landing_key


def _key():
    k = MagicMock()
    k.email = "a@b.c"
    k.expiry_time = 1000
    k.converted_tg_id = None
    k.tg_id = -1
    k.payment_id = None
    return k


def _deps():
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=True)
    xui.extend_client_key = AsyncMock(return_value=True)
    model_data = MagicMock()
    model_data.keys.update = AsyncMock()
    cache = MagicMock()
    cache.keys.set = AsyncMock()
    tariff = MagicMock(id=10, period=30, amount=99.0, name_tariff="paid", limit_ip=1)
    return xui, model_data, cache, tariff


@pytest.mark.asyncio
async def test_upgrade_records_payment_id():
    key = _key()
    xui, model_data, cache, tariff = _deps()

    result = await upgrade_landing_key(
        xui_session=xui, model_data=model_data, cache=cache, pool=MagicMock(),
        key=key, tariff=tariff, new_expiry=8000, payment_id="pay_up",
    )

    assert result is key
    assert key.payment_id == "pay_up"
    assert model_data.keys.update.await_args.args[1] is key


@pytest.mark.asyncio
async def test_upgrade_without_payment_keeps_payment_id_unset():
    """Бесплатный landing-claim (без платежа) не трогает payment_id."""
    key = _key()
    xui, model_data, cache, tariff = _deps()

    await upgrade_landing_key(
        xui_session=xui, model_data=model_data, cache=cache, pool=MagicMock(),
        key=key, tariff=tariff, new_expiry=8000,
    )

    assert key.payment_id is None
