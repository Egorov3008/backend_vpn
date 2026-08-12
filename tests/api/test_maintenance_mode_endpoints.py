"""Tests for admin maintenance-mode endpoints and PanelMaintenanceError → 503
propagation on the keys endpoints (create/trial/renew)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import app
from app.dependencies import get_pool
from services.system.maintenance import PanelMaintenanceError


class FakeConn:
    def __init__(self, store: dict):
        self._store = store

    async def fetchrow(self, query, key):
        row = self._store.get(key)
        return {"value": json.dumps(row)} if row is not None else None

    async def execute(self, query, key, value):
        self._store[key] = json.loads(value)


class FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self):
        self._store = {}

    def acquire(self):
        return FakeAcquire(FakeConn(self._store))


@pytest.fixture
def fake_pool_override(api_client):
    """Overrides get_pool with a real (in-memory) fake pool so
    MaintenanceModeService's `async with pool.acquire()` works — the shared
    api_client fixture uses a bare AsyncMock() which isn't a valid async
    context manager for `acquire()`."""
    pool = FakePool()
    app.dependency_overrides[get_pool] = lambda: pool
    yield pool


@pytest.mark.asyncio
async def test_get_maintenance_mode_default_disabled(api_client, fake_pool_override):
    response = await api_client.get("/api/v1/admin/maintenance-mode")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


@pytest.mark.asyncio
async def test_set_maintenance_mode_enabled(api_client, fake_pool_override):
    response = await api_client.post(
        "/api/v1/admin/maintenance-mode",
        json={"enabled": True, "reason": "плановые работы на панели"},
        headers={"X-API-Key": "test"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["reason"] == "плановые работы на панели"

    status = await api_client.get("/api/v1/admin/maintenance-mode")
    assert status.json()["enabled"] is True


@pytest.mark.asyncio
async def test_set_maintenance_mode_disabled_clears_reason(api_client, fake_pool_override):
    await api_client.post(
        "/api/v1/admin/maintenance-mode",
        json={"enabled": True, "reason": "работы"},
        headers={"X-API-Key": "test"},
    )
    response = await api_client.post(
        "/api/v1/admin/maintenance-mode",
        json={"enabled": False},
        headers={"X-API-Key": "test"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["reason"] is None


@pytest.mark.asyncio
async def test_renew_key_returns_503_when_panel_in_maintenance(api_client, mock_service_data):
    from models import Key

    key = Key(
        tg_id=123,
        client_id="abc123",
        email="test@vpn.ru",
        expiry_time=9999999999000,
        key="https://sub.example.com/abc",
        inbound_id=11,
        tariff_id=9,
        name_tariff="Pro",
        used_traffic=1.0,
    )
    tariff = MagicMock(id=9, amount=0, period=1)
    user = MagicMock(tg_id=123, server_id=2)

    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.servers.get_data = AsyncMock(return_value=MagicMock())

    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_renewal = MagicMock()
        mock_renewal.extension_key = AsyncMock(
            side_effect=PanelMaintenanceError("Панель на профилактике")
        )
        mock_build.return_value = (MagicMock(), mock_renewal, MagicMock())

        response = await api_client.post(
            "/api/v1/keys/test@vpn.ru/renew",
            json={"tg_id": 123, "tariff_id": 9, "number_of_months": 1},
        )

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_create_key_returns_503_when_panel_in_maintenance(api_client, mock_service_data):
    tariff = MagicMock(id=1, amount=0)
    user = MagicMock(tg_id=123, server_id=2)

    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_create_key_svc = MagicMock()
        mock_create_key_svc.proces = AsyncMock(
            side_effect=PanelMaintenanceError("Панель на профилактике")
        )
        mock_build.return_value = (mock_create_key_svc, MagicMock(), MagicMock())

        response = await api_client.post(
            "/api/v1/keys/create", json={"tg_id": 123, "tariff_id": 1}
        )

    assert response.status_code == 503
