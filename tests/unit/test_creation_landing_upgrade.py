import pytest
from unittest.mock import AsyncMock, MagicMock

from services.core.payment.creation_service import KeyCreationService


def _processor(tg_id=42, months=1, amount=100.0, conn=None):
    p = MagicMock()
    p.tg_id = tg_id
    p.number_of_months = months
    p.amount = amount
    p._conn = conn or MagicMock()
    p._model_service = MagicMock()
    p._cache = MagicMock()
    p._cache.tariffs.temporary_get = AsyncMock(return_value=None)
    p._cache.tariffs.delete = AsyncMock()
    p.extract_operation = MagicMock(return_value=["create_key", "5"])
    p._model_service.tariffs.get_data = AsyncMock(return_value=MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3))
    p._model_service.users.get_data = AsyncMock(return_value=MagicMock(tg_id=tg_id, server_id=1))
    return p


@pytest.mark.asyncio
async def test_upgrades_existing_landing_key_instead_of_creating_new():
    p = _processor()
    landing_key = MagicMock()
    landing_key.landing_uid = "abc"
    landing_key.converted_tg_id = 42
    landing_key.grace_expiry = None
    landing_key.inbound_ids = [7]
    landing_key.email = "a@b.c"
    p._model_service.keys.get_all = AsyncMock(return_value=[landing_key])

    create_key = MagicMock()
    create_key.proces = AsyncMock(return_value={"email": "new@x.c"})
    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock(return_value=MagicMock(email="a@b.c", key="k"))

    svc = KeyCreationService(processor=p, create_key=create_key, notifier=None, grace_manager=grace)
    result = await svc.process(tariff_id="5")

    grace.upgrade_from_landing.assert_awaited_once()
    create_key.proces.assert_not_awaited()
    assert result["email"] == "a@b.c"


@pytest.mark.asyncio
async def test_creates_new_key_when_no_landing_key():
    p = _processor()
    p._model_service.keys.get_all = AsyncMock(return_value=[])
    create_key = MagicMock()
    create_key.proces = AsyncMock(return_value={"email": "new@x.c"})
    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock()

    svc = KeyCreationService(processor=p, create_key=create_key, notifier=None, grace_manager=grace)
    result = await svc.process(tariff_id="5")

    create_key.proces.assert_awaited_once()
    grace.upgrade_from_landing.assert_not_awaited()
    assert result["email"] == "new@x.c"


@pytest.mark.asyncio
async def test_falls_back_to_create_when_upgrade_returns_none():
    p = _processor()
    landing_key = MagicMock()
    landing_key.landing_uid = "abc"
    landing_key.converted_tg_id = 42
    landing_key.grace_expiry = None
    landing_key.inbound_ids = [7]
    landing_key.email = "a@b.c"
    p._model_service.keys.get_all = AsyncMock(return_value=[landing_key])

    create_key = MagicMock()
    create_key.proces = AsyncMock(return_value={"email": "new@x.c"})
    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock(return_value=None)  # upgrade failed

    svc = KeyCreationService(processor=p, create_key=create_key, notifier=None, grace_manager=grace)
    result = await svc.process(tariff_id="5")

    # Landing key was found and upgrade attempted, but it failed → create a new key.
    grace.upgrade_from_landing.assert_awaited_once()
    create_key.proces.assert_awaited_once()
    assert result["email"] == "new@x.c"
