"""limit/offset/X-Total-Count on the three admin list endpoints
(admin_list_users, admin_list_keys, admin_list_payments) — same
`items[offset:offset+limit] if limit is not None else items[offset:]`
pattern as keys.py/payments.py (see tests/api/test_keys.py,
tests/api/test_payments.py); covered separately here since these are a
different router/handler set and are the actual paginating consumer
behind admin_panel/js/api.js."""
from unittest.mock import AsyncMock

import pytest

from models import User, Key, PaymentModel


def make_user(tg_id: int) -> User:
    return User(tg_id=tg_id, username=f"user{tg_id}", balance=0.0)


def make_key(email: str) -> Key:
    return Key(
        tg_id=1,
        client_id="cid",
        email=email,
        expiry_time=9999999999000,
        key="https://sub.example.com/k",
        inbound_id=11,
        tariff_id=9,
    )


def make_payment(payment_id: str) -> PaymentModel:
    return PaymentModel(payment_id=payment_id, tg_id=1, amount=10.0)


@pytest.mark.asyncio
async def test_admin_list_users_offset_applies_without_limit(api_client, mock_service_data):
    mock_service_data.users.get_all = AsyncMock(return_value=[
        make_user(1), make_user(2), make_user(3),
    ])
    response = await api_client.get("/api/v1/admin/users?offset=1")
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "3"
    data = response.json()
    assert [u["tg_id"] for u in data] == [2, 3]


@pytest.mark.asyncio
async def test_admin_list_users_limit_and_offset_combine(api_client, mock_service_data):
    mock_service_data.users.get_all = AsyncMock(return_value=[
        make_user(1), make_user(2), make_user(3),
    ])
    response = await api_client.get("/api/v1/admin/users?limit=1&offset=1")
    assert response.status_code == 200
    data = response.json()
    assert [u["tg_id"] for u in data] == [2]


@pytest.mark.asyncio
async def test_admin_list_keys_offset_applies_without_limit(api_client, mock_service_data):
    mock_service_data.keys.get_all = AsyncMock(return_value=[
        make_key("k1@vpn.ru"), make_key("k2@vpn.ru"), make_key("k3@vpn.ru"),
    ])
    response = await api_client.get("/api/v1/admin/keys?offset=1")
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "3"
    data = response.json()["keys"]
    assert [k["email"] for k in data] == ["k2@vpn.ru", "k3@vpn.ru"]


@pytest.mark.asyncio
async def test_admin_list_keys_limit_and_offset_combine(api_client, mock_service_data):
    mock_service_data.keys.get_all = AsyncMock(return_value=[
        make_key("k1@vpn.ru"), make_key("k2@vpn.ru"), make_key("k3@vpn.ru"),
    ])
    response = await api_client.get("/api/v1/admin/keys?limit=1&offset=1")
    assert response.status_code == 200
    data = response.json()["keys"]
    assert [k["email"] for k in data] == ["k2@vpn.ru"]


@pytest.mark.asyncio
async def test_admin_list_payments_offset_applies_without_limit(api_client, mock_service_data):
    mock_service_data.payments.get_all = AsyncMock(return_value=[
        make_payment("p1"), make_payment("p2"), make_payment("p3"),
    ])
    response = await api_client.get("/api/v1/admin/payments?offset=1")
    assert response.status_code == 200
    assert response.headers["X-Total-Count"] == "3"
    data = response.json()["payments"]
    assert [p["payment_id"] for p in data] == ["p2", "p3"]


@pytest.mark.asyncio
async def test_admin_list_payments_limit_and_offset_combine(api_client, mock_service_data):
    mock_service_data.payments.get_all = AsyncMock(return_value=[
        make_payment("p1"), make_payment("p2"), make_payment("p3"),
    ])
    response = await api_client.get("/api/v1/admin/payments?limit=1&offset=1")
    assert response.status_code == 200
    data = response.json()["payments"]
    assert [p["payment_id"] for p in data] == ["p2"]
