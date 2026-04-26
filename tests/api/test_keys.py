import pytest
from unittest.mock import AsyncMock
from models import Key


def make_key(email="test@vpn.ru", tg_id=123):
    return Key(
        tg_id=tg_id,
        client_id="abc123",
        email=email,
        expiry_time=9999999999000,
        key="https://sub.example.com/abc",
        inbound_id=11,
        tariff_id=9,
        name_tariff="Pro",
        total_gb=50 * (1024 ** 3),
        used_traffic=1.0,
    )


@pytest.mark.asyncio
async def test_list_keys_empty(api_client, mock_service_data):
    mock_service_data.keys.get_by = AsyncMock(return_value=None)
    response = await api_client.get("/api/v1/keys/?tg_id=123")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_keys_single(api_client, mock_service_data):
    mock_service_data.keys.get_by = AsyncMock(return_value=make_key())
    response = await api_client.get("/api/v1/keys/?tg_id=123")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["email"] == "test@vpn.ru"


@pytest.mark.asyncio
async def test_list_keys_multiple(api_client, mock_service_data):
    mock_service_data.keys.get_by = AsyncMock(return_value=[
        make_key("key1@vpn.ru"),
        make_key("key2@vpn.ru"),
    ])
    response = await api_client.get("/api/v1/keys/?tg_id=123")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_key_not_found(api_client, mock_service_data):
    mock_service_data.keys.get_data = AsyncMock(return_value=None)
    response = await api_client.get("/api/v1/keys/notexist@vpn.ru")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_key_detail(api_client, mock_service_data):
    key = make_key()
    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    response = await api_client.get("/api/v1/keys/test@vpn.ru")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@vpn.ru"
    assert "status_text" in data
    assert "days_left" in data
    assert "is_active" in data
