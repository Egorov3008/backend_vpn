import pytest
from unittest.mock import AsyncMock
from models import User


def make_user(tg_id=123, balance=0.0, trial=0, server_id=2):
    return User(
        tg_id=tg_id,
        username="testuser",
        first_name="Test",
        balance=balance,
        trial=trial,
        server_id=server_id,
    )


@pytest.mark.asyncio
async def test_get_user_not_found(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=None)
    response = await api_client.get("/api/v1/users/999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_user_found(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=make_user(tg_id=123))
    response = await api_client.get("/api/v1/users/123")
    assert response.status_code == 200
    data = response.json()
    assert data["tg_id"] == 123
    assert data["username"] == "testuser"
    assert data["balance"] == 0.0


@pytest.mark.asyncio
async def test_register_user_new(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=None)
    mock_service_data.users.save_data = AsyncMock(return_value=None)
    response = await api_client.post("/api/v1/users/register", json={
        "tg_id": 456,
        "username": "newuser",
        "first_name": "New",
        "server_id": 2,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["tg_id"] == 456


@pytest.mark.asyncio
async def test_register_user_existing(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=make_user(tg_id=456))
    response = await api_client.post("/api/v1/users/register", json={
        "tg_id": 456,
        "username": "existinguser",
    })
    assert response.status_code == 200
    assert response.json()["tg_id"] == 456


@pytest.mark.asyncio
async def test_update_user_balance(api_client, mock_service_data):
    user = make_user(tg_id=123, balance=100.0)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.users.update = AsyncMock(return_value=user)
    response = await api_client.patch("/api/v1/users/123", json={"balance": 200.0})
    assert response.status_code == 200
    assert response.json()["balance"] == 200.0


@pytest.mark.asyncio
async def test_update_user_not_found(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=None)
    response = await api_client.patch("/api/v1/users/999", json={"balance": 100.0})
    assert response.status_code == 404
