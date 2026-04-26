import pytest
from unittest.mock import AsyncMock
from models import User, Key


def make_user(tg_id=100, balance=50.0):
    return User(tg_id=tg_id, username="admin_test", balance=balance)


def make_key(email="k@vpn.ru", tg_id=100):
    return Key(
        tg_id=tg_id,
        client_id="cid",
        email=email,
        expiry_time=9999999999000,
        key="https://sub.example.com/k",
        inbound_id=11,
        tariff_id=9,
    )


@pytest.mark.asyncio
async def test_admin_stats_returns_summary(api_client, mock_service_data):
    mock_service_data.keys.get_all = AsyncMock(return_value=[make_key()])
    mock_service_data.users.get_all = AsyncMock(return_value=[make_user()])
    response = await api_client.get("/api/v1/admin/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_users" in data
    assert data["total_users"] == 1
    assert "total" in data
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_admin_list_users(api_client, mock_service_data):
    mock_service_data.users.get_all = AsyncMock(return_value=[
        make_user(tg_id=1),
        make_user(tg_id=2),
    ])
    response = await api_client.get("/api/v1/admin/users")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_admin_update_user_balance(api_client, mock_service_data):
    user = make_user(tg_id=100, balance=0.0)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.users.update = AsyncMock(return_value=user)
    response = await api_client.patch("/api/v1/admin/users/100", json={"balance": 500.0})
    assert response.status_code == 200
    assert response.json()["balance"] == 500.0


@pytest.mark.asyncio
async def test_admin_update_user_not_found(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=None)
    response = await api_client.patch("/api/v1/admin/users/999", json={"balance": 100.0})
    assert response.status_code == 404
