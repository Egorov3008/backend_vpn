import pytest
from unittest.mock import AsyncMock

from app.dependencies import get_pool
from app.main import app
from models import Key, Tariff, User
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


# --- Domain binding (allowed_domain) ------------------------------------


@pytest.mark.asyncio
async def test_public_key_without_allowed_domain_works_from_any_origin(
    api_client, fake_pool, mock_service_data
):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.tariffs.get_all = AsyncMock(
        return_value=[Tariff(id=1, name_tariff="Basic", amount=100.0)]
    )
    _, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=["tariffs:read"])

    response = await api_client.get(
        "/api/v1/public/tariffs",
        headers={"Authorization": f"Bearer {raw_key}", "Origin": "https://anything.example"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_public_key_with_allowed_domain_accepts_matching_origin(
    api_client, fake_pool, mock_service_data
):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.tariffs.get_all = AsyncMock(
        return_value=[Tariff(id=1, name_tariff="Basic", amount=100.0)]
    )
    _, raw_key = await ApiClientService(fake_pool).create(
        "Partner A", scopes=["tariffs:read"], allowed_domain="tolko-dlya-svoih.ru"
    )

    response = await api_client.get(
        "/api/v1/public/tariffs",
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Origin": "https://tolko-dlya-svoih.ru",
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_public_key_with_allowed_domain_rejects_mismatched_origin(
    api_client, fake_pool, mock_service_data
):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    _, raw_key = await ApiClientService(fake_pool).create(
        "Partner A", scopes=["tariffs:read"], allowed_domain="tolko-dlya-svoih.ru"
    )

    response = await api_client.get(
        "/api/v1/public/tariffs",
        headers={"Authorization": f"Bearer {raw_key}", "Origin": "https://evil.example"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_cors_headers_reflect_origin_on_public_prefix(
    api_client, fake_pool, mock_service_data
):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.tariffs.get_all = AsyncMock(
        return_value=[Tariff(id=1, name_tariff="Basic", amount=100.0)]
    )
    _, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=["tariffs:read"])

    response = await api_client.get(
        "/api/v1/public/tariffs",
        headers={"Authorization": f"Bearer {raw_key}", "Origin": "https://partner.example"},
    )
    assert response.headers["access-control-allow-origin"] == "https://partner.example"


# --- New resources exposed under /public/* ------------------------------


@pytest.mark.asyncio
async def test_public_keys_requires_keys_read_scope(api_client, fake_pool):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    _, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=[])

    response = await api_client.get(
        "/api/v1/public/keys/?tg_id=123", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_public_keys_list_succeeds_with_scoped_key(api_client, fake_pool, mock_service_data):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.data_service.keys.filter = AsyncMock(return_value=[])
    _, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=["keys:read"])

    response = await api_client.get(
        "/api/v1/public/keys/?tg_id=123", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_public_users_get_succeeds_with_scoped_key(api_client, fake_pool, mock_service_data):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.users.get_data = AsyncMock(
        return_value=User(tg_id=123, username="partner_user", balance=0.0, trial=0, server_id=1)
    )
    _, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=["users:read"])

    response = await api_client.get(
        "/api/v1/public/users/123", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 200
    assert response.json()["tg_id"] == 123


@pytest.mark.asyncio
async def test_public_payments_history_requires_payments_read_scope(api_client, fake_pool):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    _, raw_key = await ApiClientService(fake_pool).create("Partner A", scopes=[])

    response = await api_client.get(
        "/api/v1/public/payments/?tg_id=123", headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 403
