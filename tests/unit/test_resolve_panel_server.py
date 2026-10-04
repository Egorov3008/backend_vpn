"""Сервер панели выбирается по XUI_SERVER_ID, а не по users.server_id."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import settings
from models.servers.server import resolve_panel_server
from services.core.payment.creation_service import KeyCreationService
from services.core.payment.renewal_service import KeyRenewalService


def _strict_servers(row=None):
    """servers.get_data как в BaseData: пустой identifier -> ValueError."""
    async def get_data(identifier, conn=None):
        if not identifier:
            raise ValueError("BaseData: identifier is required")
        return row if row is not None and identifier == row.id else None

    servers = MagicMock()
    servers.get_data = AsyncMock(side_effect=get_data)
    return servers


@pytest.mark.asyncio
async def test_resolve_returns_db_row_for_xui_server_id():
    row = MagicMock(id=settings.xui_server_id)
    assert await resolve_panel_server(_strict_servers(row)) is row


@pytest.mark.asyncio
async def test_resolve_falls_back_to_env_when_row_missing():
    server = await resolve_panel_server(_strict_servers())
    assert server.api_url == settings.api_url
    assert server.login == settings.admin_username


@pytest.mark.asyncio
async def test_paid_create_ignores_user_server_id():
    p = MagicMock()
    p.tg_id, p.number_of_months, p.amount = 42, 1, 100.0
    p._conn = MagicMock()
    p._model_service = MagicMock()
    p._model_service.keys.get_all = AsyncMock(return_value=[])
    p._cache.tariffs.temporary_get = AsyncMock(return_value=None)
    p._cache.tariffs.delete = AsyncMock()
    p.extract_operation = MagicMock(return_value=["create_key", "5"])
    p._model_service.tariffs.get_data = AsyncMock(
        return_value=MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3)
    )
    p._model_service.users.get_data = AsyncMock(return_value=MagicMock(tg_id=42, server_id=None))
    create_key = MagicMock()
    create_key.proces = AsyncMock(return_value={"email": "new@x.c"})

    svc = KeyCreationService(processor=p, create_key=create_key, notifier=None,
                             xui_session=MagicMock(), cache=MagicMock(), pool=MagicMock())
    with patch("services.core.payment.creation_service.upgrade_landing_key", AsyncMock()):
        await svc.process(tariff_id="5")

    assert create_key.proces.call_args.kwargs["server_id"] == settings.xui_server_id


@pytest.mark.asyncio
async def test_paid_renew_works_for_user_without_server_id():
    p = MagicMock()
    p.tg_id, p.number_of_months, p.amount = 42, 1, 100.0
    p._conn = MagicMock()
    p._model_service = MagicMock()
    p._model_service.servers = _strict_servers()
    p._model_service.users.get_data = AsyncMock(return_value=MagicMock(tg_id=42, server_id=None))
    p._model_service.tariffs.get_data = AsyncMock(return_value=MagicMock(id=5, amount=100.0))
    p._cache.tariffs.temporary_get = AsyncMock(return_value=None)
    p._cache.tariffs.delete = AsyncMock()
    p.extract_operation = MagicMock(return_value=["renew_key", "k@x"])
    key = MagicMock(email="k@x", tg_id=42, tariff_id=5)
    p._model_service.keys.get_data = AsyncMock(return_value=key)
    key_manager = MagicMock()
    key_manager.extension_key = AsyncMock(return_value=MagicMock(expiry_time=1_800_000_000_000, key="sub"))

    svc = KeyRenewalService(processor=p, key_manager=key_manager)
    await svc.process(email="k@x")

    server = key_manager.extension_key.call_args.kwargs["server"]
    assert server.api_url == settings.api_url
