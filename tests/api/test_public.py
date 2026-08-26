import pytest
from unittest.mock import AsyncMock

from app.dependencies import get_pool
from app.main import app
from models import Tariff
from services.api_clients.service import ApiClientService
from tests.services.test_api_clients_service import FakeApiClientsPool


@pytest.fixture
def fake_pool():
    return FakeApiClientsPool()


@pytest.mark.asyncio
async def test_public_tariffs_requires_auth(api_client, fake_pool):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    response = await api_client.get("/api/v1/public/tariffs")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_public_tariffs_rejects_invalid_key(api_client, fake_pool):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    response = await api_client.get(
        "/api/v1/public/tariffs", headers={"Authorization": "Bearer pub_totally-fake"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_public_tariffs_rejects_missing_scope(api_client, fake_pool):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    _, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=[])

    response = await api_client.get(
        "/api/v1/public/tariffs", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_tariffs_succeeds_with_valid_scoped_key(api_client, fake_pool, mock_service_data):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.tariffs.get_all = AsyncMock(
        return_value=[Tariff(id=1, name_tariff="Basic", amount=100.0)]
    )
    _, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=["tariffs:read"])

    response = await api_client.get(
        "/api/v1/public/tariffs", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name_tariff"] == "Basic"


@pytest.mark.asyncio
async def test_public_tariffs_rejects_revoked_key(api_client, fake_pool):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    client, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=["tariffs:read"])
    await ApiClientService(fake_pool).revoke(client.id)

    response = await api_client.get(
        "/api/v1/public/tariffs", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 401
