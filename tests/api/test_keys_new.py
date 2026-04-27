import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from models import Key, User, Tariff, Server


def make_key(email="test@vpn.ru", tg_id=123):
    return Key(
        tg_id=tg_id,
        client_id="abc123",
        email=email,
        expiry_time=9999999999000,
        key="https://sub.example.com/abc",
        inbound_id=11,
        tariff_id=1,
        name_tariff="Free",
        total_gb=50 * (1024 ** 3),
        used_traffic=1.0,
    )


def make_user(tg_id=123):
    return User(
        tg_id=tg_id,
        username="testuser",
    )


def make_tariff(tariff_id=1, amount=0, name="Free"):
    return Tariff(
        id=tariff_id,
        name_tariff=name,
        amount=amount,
    )


def make_server(server_id=2):
    return Server(
        id=server_id,
        cluster_name="cluster-1",
        server_name="server-1",
        api_url="http://localhost:9070",
        subscription_url="http://localhost:9070/sub",
        login="admin",
        password="pass123",
    )


@pytest.mark.asyncio
async def test_create_key_free_tariff(api_client, mock_service_data):
    """Test creating a key with free tariff succeeds"""
    user = make_user()
    tariff = make_tariff()
    created_key = make_key()

    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.keys.get_data = AsyncMock(return_value=created_key)

    # Mock the build_key_services to return mocked services
    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_create_key_svc = MagicMock()
        mock_create_key_svc.proces = AsyncMock(return_value={
            "email": "test@vpn.ru",
            "public_link": "https://sub.example.com/abc",
            "days": 30,
            "link_to_connect": "https://tds-pro.space/vless/abc",
        })
        mock_build.return_value = (mock_create_key_svc, MagicMock(), MagicMock())

        response = await api_client.post("/api/v1/keys/create", json={
            "tg_id": 123,
            "tariff_id": 1,
        })

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@vpn.ru"
    assert data["tg_id"] == 123
    assert data["client_id"] == "abc123"
    mock_create_key_svc.proces.assert_called_once()


@pytest.mark.asyncio
async def test_create_key_paid_tariff(api_client, mock_service_data):
    """Test creating a key with paid tariff fails with 402"""
    user = make_user()
    tariff = make_tariff(amount=100)

    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    response = await api_client.post("/api/v1/keys/create", json={
        "tg_id": 123,
        "tariff_id": 2,
    })

    assert response.status_code == 402
    assert "Only free tariffs are allowed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_key_user_not_found(api_client, mock_service_data):
    """Test creating a key for non-existent user fails"""
    tariff = make_tariff()

    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)
    mock_service_data.users.get_data = AsyncMock(return_value=None)

    response = await api_client.post("/api/v1/keys/create", json={
        "tg_id": 999,
        "tariff_id": 1,
    })

    assert response.status_code == 404
    assert "User not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_key_tariff_not_found(api_client, mock_service_data):
    """Test creating a key with non-existent tariff fails"""
    mock_service_data.tariffs.get_data = AsyncMock(return_value=None)

    response = await api_client.post("/api/v1/keys/create", json={
        "tg_id": 123,
        "tariff_id": 999,
    })

    assert response.status_code == 404
    assert "Tariff not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_key(api_client, mock_service_data):
    """Test deleting a key calls delete_client and removes from DB"""
    key = make_key()
    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.keys.delete = AsyncMock(return_value=True)

    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_xui = AsyncMock()
        mock_xui.delete_client = AsyncMock(return_value=True)
        mock_build.return_value = (MagicMock(), MagicMock(), mock_xui)

        response = await api_client.delete("/api/v1/keys/test@vpn.ru?tg_id=123")

    assert response.status_code == 204
    mock_xui.delete_client.assert_called_once_with("test@vpn.ru", 11, "abc123")
    mock_service_data.keys.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_key_not_found(api_client, mock_service_data):
    """Test deleting a non-existent key fails"""
    mock_service_data.keys.get_data = AsyncMock(return_value=None)

    response = await api_client.delete("/api/v1/keys/notexist@vpn.ru?tg_id=123")

    assert response.status_code == 404
    assert "Key not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_key_wrong_user(api_client, mock_service_data):
    """Test deleting a key that doesn't belong to user fails"""
    key = make_key(tg_id=456)  # Different user
    mock_service_data.keys.get_data = AsyncMock(return_value=key)

    response = await api_client.delete("/api/v1/keys/test@vpn.ru?tg_id=123")

    assert response.status_code == 403
    assert "Key does not belong to this user" in response.json()["detail"]


@pytest.mark.asyncio
async def test_renew_key_free(api_client, mock_service_data):
    """Test renewing a key with free tariff succeeds"""
    key = make_key()
    tariff = make_tariff()
    server = make_server()
    renewed_key = make_key()

    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)
    mock_service_data.servers.get_data = AsyncMock(return_value=server)

    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_renewal = AsyncMock()
        mock_build.return_value = (MagicMock(), mock_renewal, MagicMock())

        # Mock the second get_data call (after renewal)
        mock_service_data.keys.get_data = AsyncMock(side_effect=[key, renewed_key])

        response = await api_client.post("/api/v1/keys/test@vpn.ru/renew", json={
            "tg_id": 123,
            "tariff_id": 1,
            "number_of_months": 3,
        })

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@vpn.ru"
    assert data["tg_id"] == 123
    mock_renewal.extension_key.assert_called_once()


@pytest.mark.asyncio
async def test_renew_key_paid(api_client, mock_service_data):
    """Test renewing a key with paid tariff fails with 402"""
    key = make_key()
    tariff = make_tariff(amount=100)

    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)

    response = await api_client.post("/api/v1/keys/test@vpn.ru/renew", json={
        "tg_id": 123,
        "tariff_id": 2,
        "number_of_months": 3,
    })

    assert response.status_code == 402
    assert "Only free tariffs are allowed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_renew_key_not_found(api_client, mock_service_data):
    """Test renewing a non-existent key fails"""
    mock_service_data.keys.get_data = AsyncMock(return_value=None)

    response = await api_client.post("/api/v1/keys/notexist@vpn.ru/renew", json={
        "tg_id": 123,
        "tariff_id": 1,
        "number_of_months": 3,
    })

    assert response.status_code == 404
    assert "Key not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_renew_key_wrong_user(api_client, mock_service_data):
    """Test renewing a key that doesn't belong to user fails"""
    key = make_key(tg_id=456)  # Different user
    mock_service_data.keys.get_data = AsyncMock(return_value=key)

    response = await api_client.post("/api/v1/keys/test@vpn.ru/renew", json={
        "tg_id": 123,
        "tariff_id": 1,
        "number_of_months": 3,
    })

    assert response.status_code == 403
    assert "Key does not belong to this user" in response.json()["detail"]
