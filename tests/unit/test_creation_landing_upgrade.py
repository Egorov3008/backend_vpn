import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.core.payment.creation_service import KeyCreationService
from services.core.keys.utils.inbounds import BASELINE_INBOUNDS


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


def _svc(processor, create_key):
    return KeyCreationService(
        processor=processor,
        create_key=create_key,
        notifier=None,
        xui_session=MagicMock(),
        cache=MagicMock(),
        pool=MagicMock(),
    )


@pytest.mark.asyncio
async def test_upgrades_existing_landing_key_instead_of_creating_new():
    p = _processor()
    landing_key = MagicMock()
    landing_key.landing_uid = "abc"
    landing_key.converted_tg_id = 42
    landing_key.inbound_ids = list(BASELINE_INBOUNDS)
    landing_key.email = "a@b.c"
    p._model_service.keys.get_all = AsyncMock(return_value=[landing_key])

    create_key = MagicMock()
    create_key.proces = AsyncMock(return_value={"email": "new@x.c"})
    upgraded = MagicMock(email="a@b.c", key="k")

    svc = _svc(p, create_key)
    with patch(
        "services.core.payment.creation_service.upgrade_landing_key",
        AsyncMock(return_value=upgraded),
    ) as upgrade_mock:
        result = await svc.process(tariff_id="5")

    upgrade_mock.assert_awaited_once()
    _, kwargs = upgrade_mock.call_args
    assert kwargs["xui_session"] is svc.xui_session
    assert kwargs["model_data"] is p._model_service
    assert kwargs["cache"] is svc.cache
    assert kwargs["pool"] is svc.pool
    assert kwargs["key"] is landing_key
    assert kwargs["transfer_tg"] is True
    create_key.proces.assert_not_awaited()
    assert result["email"] == "a@b.c"


@pytest.mark.asyncio
async def test_creates_new_key_when_no_landing_key():
    p = _processor()
    p._model_service.keys.get_all = AsyncMock(return_value=[])
    create_key = MagicMock()
    create_key.proces = AsyncMock(return_value={"email": "new@x.c"})

    svc = _svc(p, create_key)
    with patch(
        "services.core.payment.creation_service.upgrade_landing_key",
        AsyncMock(),
    ) as upgrade_mock:
        result = await svc.process(tariff_id="5")

    create_key.proces.assert_awaited_once()
    upgrade_mock.assert_not_awaited()
    assert result["email"] == "new@x.c"


@pytest.mark.asyncio
async def test_falls_back_to_create_when_upgrade_returns_none():
    p = _processor()
    landing_key = MagicMock()
    landing_key.landing_uid = "abc"
    landing_key.converted_tg_id = 42
    landing_key.inbound_ids = list(BASELINE_INBOUNDS)
    landing_key.email = "a@b.c"
    p._model_service.keys.get_all = AsyncMock(return_value=[landing_key])

    create_key = MagicMock()
    create_key.proces = AsyncMock(return_value={"email": "new@x.c"})

    svc = _svc(p, create_key)
    with patch(
        "services.core.payment.creation_service.upgrade_landing_key",
        AsyncMock(return_value=None),
    ) as upgrade_mock:
        result = await svc.process(tariff_id="5")

    # Landing key was found and upgrade attempted, but it failed → create a new key.
    upgrade_mock.assert_awaited_once()
    create_key.proces.assert_awaited_once()
    assert result["email"] == "new@x.c"


@pytest.mark.asyncio
async def test_no_landing_upgrade_when_xui_session_not_wired():
    """xui_session/cache/pool not provided → landing-upgrade path skipped entirely."""
    p = _processor()
    landing_key = MagicMock()
    landing_key.landing_uid = "abc"
    landing_key.converted_tg_id = 42
    landing_key.inbound_ids = list(BASELINE_INBOUNDS)
    landing_key.email = "a@b.c"
    p._model_service.keys.get_all = AsyncMock(return_value=[landing_key])

    create_key = MagicMock()
    create_key.proces = AsyncMock(return_value={"email": "new@x.c"})

    svc = KeyCreationService(processor=p, create_key=create_key, notifier=None)
    with patch(
        "services.core.payment.creation_service.upgrade_landing_key",
        AsyncMock(),
    ) as upgrade_mock:
        result = await svc.process(tariff_id="5")

    upgrade_mock.assert_not_awaited()
    create_key.proces.assert_awaited_once()
    assert result["email"] == "new@x.c"
