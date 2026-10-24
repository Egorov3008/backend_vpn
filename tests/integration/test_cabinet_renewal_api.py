"""Продление из кабинета (Mini App и веб) по тарифу, снятому с продажи.

Тариф ключа мог пропасть из AVAILABLE_RATES: новые ключи на нём не продаются,
но владелец ключа должен продлить его по цене своего тарифа.
"""
import time

import pytest

from tests.integration.conftest import STANDARD_TARIFF_ID, TRIAL_TARIFF_ID
from tests.integration.helpers import db_key, db_payment, insert_key, register_user

DAY_MS = 24 * 3600 * 1000
LEGACY_TARIFF_ID = 20
LEGACY_PRICE = 150.0
WEB_HEADERS = {"X-Requested-With": "web"}


@pytest.fixture
async def legacy_key(backend):
    """Пользователь 7001 с ключом на тарифе, которого нет в AVAILABLE_RATES."""
    await backend.pool.execute(
        "INSERT INTO tariff (id, name_tariff, amount, description, limit_ip, period, traffic_limit)"
        " VALUES ($1, 'Старый', $2, 'Снят с продажи', 1, 30, 0)",
        LEGACY_TARIFF_ID,
        LEGACY_PRICE,
    )
    await register_user(backend, 7001)
    email = await insert_key(
        backend, 7001, tariff_id=LEGACY_TARIFF_ID, expiry_time=int(time.time() * 1000) + DAY_MS
    )
    await backend.reload_cache()
    return email


def _session(tg_id: int) -> dict:
    from app.core.miniapp_session import web_sessions

    token, _ = web_sessions.issue(tg_id)
    return {**WEB_HEADERS, "Cookie": f"web_session={token}"}


async def test_tariffs_include_own_legacy_tariff_as_renew_only(backend, legacy_key):
    resp = await backend.client.get("/api/v1/web/tariffs", headers=_session(7001))

    assert resp.status_code == 200, resp.text
    by_id = {t["id"]: t for t in resp.json()}
    assert by_id[LEGACY_TARIFF_ID]["renew_only"] is True
    assert by_id[LEGACY_TARIFF_ID]["amount"] == LEGACY_PRICE
    assert by_id[STANDARD_TARIFF_ID]["renew_only"] is False
    assert TRIAL_TARIFF_ID not in by_id


async def test_other_users_do_not_see_legacy_tariff(backend, legacy_key):
    await register_user(backend, 7002)

    resp = await backend.client.get("/api/v1/web/tariffs", headers=_session(7002))

    assert LEGACY_TARIFF_ID not in {t["id"] for t in resp.json()}


async def test_renewal_on_legacy_tariff_is_billed_at_its_price(backend, legacy_key):
    headers = _session(7001)
    body = {"tariff_id": LEGACY_TARIFF_ID, "number_of_months": 1, "operation": "renew_key", "email": legacy_key}
    before = (await db_key(backend, legacy_key))["expiry_time"]

    quote = await backend.client.post("/api/v1/web/payments/quotes", json=body, headers=headers)
    invoice = await backend.client.post("/api/v1/web/payments", json=body, headers=headers)

    assert quote.status_code == 200, quote.text
    assert invoice.status_code == 200, invoice.text
    assert quote.json()["final_amount"] == invoice.json()["amount"] == LEGACY_PRICE

    payment_id = invoice.json()["payment_id"]
    backend.fakes.set_payment_status(payment_id, "succeeded")
    await backend.client.post(
        "/api/v1/payments/webhook",
        json={"type": "notification", "event": "payment.succeeded", "object": {"id": payment_id}},
    )

    assert (await db_payment(backend, payment_id))["status"] == "succeeded"
    key = await db_key(backend, legacy_key)
    assert key["tariff_id"] == LEGACY_TARIFF_ID
    assert abs(key["expiry_time"] - (before + 30 * DAY_MS)) < 60_000


async def test_legacy_tariff_not_sold_as_new_key(backend, legacy_key):
    body = {"tariff_id": LEGACY_TARIFF_ID, "number_of_months": 1, "operation": "create_key"}

    resp = await backend.client.post("/api/v1/web/payments", json=body, headers=_session(7001))

    assert resp.status_code == 404


async def test_legacy_tariff_not_allowed_for_another_key(backend, legacy_key):
    """Чужой тариф снятый с продажи нельзя подставить при продлении своего ключа."""
    await register_user(backend, 7003)
    own = await insert_key(
        backend, 7003, tariff_id=STANDARD_TARIFF_ID, expiry_time=int(time.time() * 1000) + DAY_MS
    )
    await backend.reload_cache()
    body = {"tariff_id": LEGACY_TARIFF_ID, "number_of_months": 1, "operation": "renew_key", "email": own}

    resp = await backend.client.post("/api/v1/web/payments", json=body, headers=_session(7003))

    assert resp.status_code == 404
