import pytest

from app.dependencies import get_pool
from tests.services.test_api_clients_service import FakeApiClientsPool


@pytest.fixture
def fake_pool():
    return FakeApiClientsPool()


@pytest.mark.asyncio
async def test_create_api_client_returns_key_once(api_client, fake_pool):
    from app.main import app
    app.dependency_overrides[get_pool] = lambda: fake_pool

    response = await api_client.post(
        "/api/v1/admin/api-clients",
        json={"name": "Partner A", "scopes": ["tariffs:read"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["api_key"].startswith("pub_")
    assert body["name"] == "Partner A"
    assert body["scopes"] == ["tariffs:read"]
    assert "key_hash" not in body


@pytest.mark.asyncio
async def test_list_api_clients_excludes_secret(api_client, fake_pool):
    from app.main import app
    app.dependency_overrides[get_pool] = lambda: fake_pool

    await api_client.post("/api/v1/admin/api-clients", json={"name": "Partner A", "scopes": []})
    response = await api_client.get("/api/v1/admin/api-clients")
    assert response.status_code == 200
    clients = response.json()["clients"]
    assert len(clients) == 1
    assert clients[0]["name"] == "Partner A"
    assert "key_hash" not in clients[0]
    assert "api_key" not in clients[0]


@pytest.mark.asyncio
async def test_revoke_api_client(api_client, fake_pool):
    from app.main import app
    app.dependency_overrides[get_pool] = lambda: fake_pool

    created = (await api_client.post(
        "/api/v1/admin/api-clients", json={"name": "Partner A", "scopes": []}
    )).json()

    response = await api_client.post(f"/api/v1/admin/api-clients/{created['id']}/revoke")
    assert response.status_code == 200
    assert response.json()["is_active"] is False


@pytest.mark.asyncio
async def test_revoke_unknown_api_client_returns_404(api_client, fake_pool):
    from app.main import app
    app.dependency_overrides[get_pool] = lambda: fake_pool

    response = await api_client.post("/api/v1/admin/api-clients/999/revoke")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rotate_api_client_issues_new_key(api_client, fake_pool):
    from app.main import app
    app.dependency_overrides[get_pool] = lambda: fake_pool

    created = (await api_client.post(
        "/api/v1/admin/api-clients", json={"name": "Partner A", "scopes": ["tariffs:read"]}
    )).json()

    response = await api_client.post(f"/api/v1/admin/api-clients/{created['id']}/rotate")
    assert response.status_code == 200
    body = response.json()
    assert body["api_key"].startswith("pub_")
    assert body["api_key"] != created["api_key"]
    assert body["scopes"] == ["tariffs:read"]
