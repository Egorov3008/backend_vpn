"""Фильтры админ-списков: X-Total-Count считает отфильтрованное, пагинация после фильтра."""
import time
from unittest.mock import AsyncMock

from models.keys.key import Key
from models.payments.payment import PaymentModel
from models.users.user import User

DAY = 24 * 3600 * 1000


def _key(email, tg_id, days):
    now = int(time.time() * 1000)
    return Key(tg_id=tg_id, client_id="c", email=email, expiry_time=now + days * DAY, key="vless://x", inbound_id=1)


async def test_users_filter(api_client, mock_service_data):
    mock_service_data.users.get_all = AsyncMock(
        return_value=[User(tg_id=1, username="ivan"), User(tg_id=2, username="anna"), User(tg_id=3, username="ivanka")]
    )
    resp = await api_client.get("/api/v1/admin/users", params={"q": "ivan", "limit": 1})
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "2"
    assert [u["tg_id"] for u in resp.json()] == [1]


async def test_keys_filter(api_client, mock_service_data):
    mock_service_data.keys.get_all = AsyncMock(
        return_value=[_key("a1", 1, 30), _key("a2", 1, -1), _key("b1", 2, 1)]
    )
    resp = await api_client.get("/api/v1/admin/keys", params={"status": "expired"})
    assert resp.headers["X-Total-Count"] == "1"
    assert [k["email"] for k in resp.json()["keys"]] == ["a2"]

    resp = await api_client.get("/api/v1/admin/keys", params={"tg_id": 2, "status": "expiring"})
    assert [k["email"] for k in resp.json()["keys"]] == ["b1"]

    resp = await api_client.get("/api/v1/admin/keys", params={"status": "bogus"})
    assert resp.status_code == 422


async def test_payments_filter(api_client, mock_service_data):
    mock_service_data.payments.get_all = AsyncMock(
        return_value=[
            PaymentModel(payment_id="p1", tg_id=1, status="succeeded"),
            PaymentModel(payment_id="p2", tg_id=1, status="pending"),
            PaymentModel(payment_id="p3", tg_id=2, status="succeeded"),
        ]
    )
    resp = await api_client.get("/api/v1/admin/payments", params={"status": "succeeded"})
    assert resp.headers["X-Total-Count"] == "2"
    assert [p["payment_id"] for p in resp.json()["payments"]] == ["p1", "p3"]
