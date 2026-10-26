"""Продление ключа по прямой ссылке без входа (`/api/v1/web/renew/{email}`).

Ссылка стоит в подписке ключа (3x-ui Profile page) и открывается из Happ в
браузере, без Telegram и без веб-сессии. Платёж записывается на владельца
ключа; баланс владельца по ссылке не списывается и не виден.
"""
import time

import pytest

from tests.integration.conftest import FAMILY_TARIFF_ID, STANDARD_TARIFF_ID
from tests.integration.helpers import db_key, db_payment, insert_key, issue_trial, register_user

DAY_MS = 24 * 3600 * 1000
LEGACY_TARIFF_ID = 20
OWNER = 8001


@pytest.fixture
async def owner_key(backend):
    await register_user(backend, OWNER)
    email = await insert_key(
        backend, OWNER, tariff_id=STANDARD_TARIFF_ID, expiry_time=int(time.time() * 1000) + DAY_MS
    )
    await backend.reload_cache()
    return email


async def _pay(backend, email: str, tariff_id: int = STANDARD_TARIFF_ID) -> str:
    resp = await backend.client.post(
        f"/api/v1/web/renew/{email}/payments", json={"tariff_id": tariff_id, "number_of_months": 1}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["payment_id"]


async def _succeed(backend, payment_id: str) -> None:
    backend.fakes.set_payment_status(payment_id, "succeeded")
    await backend.client.post(
        "/api/v1/payments/webhook",
        json={"type": "notification", "event": "payment.succeeded", "object": {"id": payment_id}},
    )


async def test_key_info_shows_key_and_tariffs_without_owner_data(backend, owner_key):
    resp = await backend.client.get(f"/api/v1/web/renew/{owner_key}")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["email"] == owner_key
    assert data["tariff_id"] == STANDARD_TARIFF_ID
    assert {STANDARD_TARIFF_ID, FAMILY_TARIFF_ID} <= {t["id"] for t in data["tariffs"]}
    assert str(OWNER) not in resp.text


async def test_anyone_with_link_renews_the_key(backend, owner_key):
    before = (await db_key(backend, owner_key))["expiry_time"]

    payment_id = await _pay(backend, owner_key)
    await _succeed(backend, payment_id)

    payment = await db_payment(backend, payment_id)
    assert payment["tg_id"] == OWNER
    assert payment["status"] == "succeeded"
    key = await db_key(backend, owner_key)
    assert abs(key["expiry_time"] - (before + 30 * DAY_MS)) < 60_000
    status = await backend.client.get(f"/api/v1/web/renew/{owner_key}/payments/{payment_id}/status")
    assert status.status_code == 200
    assert status.json()["processed"] is True
    assert "tg_id" not in status.json()


async def test_owner_balance_is_not_spent_or_shown(backend, owner_key):
    await backend.pool.execute("UPDATE users SET balance = 50 WHERE tg_id = $1", OWNER)
    await backend.reload_cache()
    body = {"tariff_id": STANDARD_TARIFF_ID, "number_of_months": 1}

    quote = await backend.client.post(f"/api/v1/web/renew/{owner_key}/quotes", json=body)
    payment_id = await _pay(backend, owner_key)
    await _succeed(backend, payment_id)

    assert quote.status_code == 200, quote.text
    assert quote.json()["balance_discount_amount"] == 0
    assert (await db_payment(backend, payment_id))["balance_discount"] == 0
    balance = await backend.pool.fetchval("SELECT balance FROM users WHERE tg_id = $1", OWNER)
    assert balance == 50


async def test_unknown_key_is_not_found(backend):
    resp = await backend.client.get("/api/v1/web/renew/nosuch")
    pay = await backend.client.post(
        "/api/v1/web/renew/nosuch/payments", json={"tariff_id": STANDARD_TARIFF_ID, "number_of_months": 1}
    )

    assert resp.status_code == 404
    assert pay.status_code == 404


async def test_landing_guest_key_is_not_renewed_by_link(backend):
    resp = await backend.client.post("/api/v1/web/anonymous/key", headers={"X-Requested-With": "web"})
    assert resp.status_code == 200, resp.text
    email = await backend.pool.fetchval(
        "SELECT email FROM keys WHERE landing_uid = $1", resp.json()["landing_uid"]
    )
    assert email

    info = await backend.client.get(f"/api/v1/web/renew/{email}")

    assert info.status_code == 404


async def test_off_sale_tariff_renews_only_at_its_own_tariff(backend):
    await backend.pool.execute(
        "INSERT INTO tariff (id, name_tariff, amount, description, limit_ip, period, traffic_limit)"
        " VALUES ($1, 'Старый', 150, 'Снят с продажи', 1, 30, 0)",
        LEGACY_TARIFF_ID,
    )
    await register_user(backend, 8002)
    email = await insert_key(
        backend, 8002, tariff_id=LEGACY_TARIFF_ID, expiry_time=int(time.time() * 1000) + DAY_MS
    )
    await backend.reload_cache()

    info = await backend.client.get(f"/api/v1/web/renew/{email}")
    switch = await backend.client.post(
        f"/api/v1/web/renew/{email}/payments", json={"tariff_id": STANDARD_TARIFF_ID, "number_of_months": 1}
    )

    assert [(t["id"], t["renew_only"]) for t in info.json()["tariffs"]] == [(LEGACY_TARIFF_ID, True)]
    assert switch.status_code == 404


async def test_trial_key_renews_at_tariff_on_sale(backend):
    trial = await issue_trial(backend, 8003)

    payment_id = await _pay(backend, trial["email"], FAMILY_TARIFF_ID)

    assert (await db_payment(backend, payment_id))["payment_type"] == f"renew_key|{trial['email']}"


async def test_status_of_other_owner_payments_is_hidden(backend, owner_key):
    other = await backend.client.post(
        "/api/v1/payments/create",
        json={"tg_id": OWNER, "tariff_id": STANDARD_TARIFF_ID, "number_of_months": 1, "operation": "create_key"},
    )
    assert other.status_code == 200, other.text

    resp = await backend.client.get(
        f"/api/v1/web/renew/{owner_key}/payments/{other.json()['payment_id']}/status"
    )

    assert resp.status_code == 404
