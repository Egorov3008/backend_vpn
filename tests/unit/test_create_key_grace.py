import pytest
from unittest.mock import AsyncMock, MagicMock
from services.core.keys.utils.create_key import CreateKey


def _make_key(grace_expiry):
    k = MagicMock()
    k.client_id = "uuid-1"
    k.email = "a@b.c"
    k.tg_id = 1
    k.limit_ip = 1
    k.inbound_ids = [7, 11, 12]
    k.inbound_id = 7
    k.expiry_time = 2000
    k.grace_expiry = grace_expiry
    k.key = "https://sub.example/a@b.c"
    return k


def _create_key(key):
    model_data = MagicMock()
    model_data.keys.save_data = AsyncMock()
    xui = MagicMock()
    xui.add_client = AsyncMock(return_value=True)
    expiry = MagicMock()
    formation = MagicMock()
    formation.form_new_key = AsyncMock(return_value=key)
    ck = CreateKey(model_data=model_data, xui_session=xui, expiry=expiry, formation=formation)
    return ck, xui


@pytest.mark.asyncio
async def test_subscription_uses_grace_expiry_as_panel_expiry():
    ck, xui = _create_key(_make_key(grace_expiry=9000))
    tariff = MagicMock(id=5, amount=100.0, limit_ip=1, period=1)
    await ck.proces(tg_id=1, tariff=tariff, server_id=2, conn=MagicMock())
    _, kwargs = xui.add_client.call_args
    assert kwargs["expiry_time"] == 9000


@pytest.mark.asyncio
async def test_non_subscription_uses_expiry_time():
    ck, xui = _create_key(_make_key(grace_expiry=None))
    tariff = MagicMock(id=2, amount=0.0, limit_ip=1, period=1)
    await ck.proces(tg_id=1, tariff=tariff, server_id=2, conn=MagicMock())
    _, kwargs = xui.add_client.call_args
    assert kwargs["expiry_time"] == 2000
