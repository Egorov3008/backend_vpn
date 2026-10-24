"""Промокоды: админ создаёт код, пользователь вводит его в кабинете, оплата засчитывает активацию.

Проверяем сумму счёта, строки в promo_redemptions/payments и ответы API, а не вызовы.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.conftest import FAMILY_TARIFF_ID, STANDARD_TARIFF_ID, TARIFFS
from tests.integration.helpers import db_payment, issue_trial, register_user

WEB_HEADERS = {"X-Requested-With": "web"}
ADMIN_TG_ID = 9001
PRICE = {tariff_id: amount for tariff_id, _, amount, *_ in TARIFFS}


def _session(tg_id: int) -> dict:
    from app.core.miniapp_session import web_sessions

    token, _ = web_sessions.issue(tg_id)
    return {**WEB_HEADERS, "Cookie": f"web_session={token}"}


def _admin_headers() -> dict:
    from config import settings

    return {"X-API-Key": settings.admin_api_key, "X-Admin-Tg-Id": str(ADMIN_TG_ID)}


async def _create_code(backend, **overrides) -> dict:
    now = datetime.now(timezone.utc)
    body = {
        "code": "SVOI30",
        "discount_percent": 30,
        "starts_at": (now - timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(days=1)).isoformat(),
        **overrides,
    }
    resp = await backend.client.post("/api/v1/admin/promo-codes", json=body, headers=_admin_headers())
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _quote(backend, tg_id, code="SVOI30", **body):
    resp = await backend.client.post(
        "/api/v1/web/payments/quotes",
        json={"tariff_id": STANDARD_TARIFF_ID, "promo_code": code, **body},
        headers=_session(tg_id),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _invoice(backend, tg_id, code="SVOI30", **body):
    return await backend.client.post(
        "/api/v1/web/payments",
        json={"tariff_id": STANDARD_TARIFF_ID, "promo_code": code, **body},
        headers=_session(tg_id),
    )


async def _pay(backend, payment_id):
    backend.fakes.set_payment_status(payment_id, "succeeded")
    resp = await backend.client.post(
        "/api/v1/payments/webhook",
        json={"type": "notification", "event": "payment.succeeded", "object": {"id": payment_id}},
    )
    assert resp.status_code == 200, resp.text


async def _redemption(backend, promo_id, tg_id):
    row = await backend.pool.fetchrow(
        "SELECT * FROM promo_redemptions WHERE promo_id = $1 AND tg_id = $2", promo_id, tg_id
    )
    return dict(row) if row else None


# --- применение кода -----------------------------------------------------------


async def test_new_user_pays_with_code_and_activation_counts(backend):
    promo = await _create_code(backend)
    await issue_trial(backend, 8001)  # пробный ключ не делает пользователя «не новым»

    quote = await _quote(backend, 8001, code=" svoi30 ")
    discount = round(PRICE[STANDARD_TARIFF_ID] * 0.30, 2)
    assert quote["promo_status"] == "applied"
    assert quote["promo_code"] == "SVOI30"
    assert quote["promo_discount_amount"] == discount
    assert quote["final_amount"] == round(PRICE[STANDARD_TARIFF_ID] - discount, 2)

    resp = await _invoice(backend, 8001, code="svoi30")
    assert resp.status_code == 200, resp.text
    invoice = resp.json()
    assert invoice["amount"] == quote["final_amount"]
    assert backend.fakes.state()["yk_requests"][-1]["amount"]["value"] == f"{quote['final_amount']:.2f}"
    payment = await db_payment(backend, invoice["payment_id"])
    assert payment["promo_code_id"] == promo["id"]
    assert payment["promo_discount"] == pytest.approx(discount)
    assert (await _redemption(backend, promo["id"], 8001))["status"] == "reserved"

    await _pay(backend, invoice["payment_id"])

    redemption = await _redemption(backend, promo["id"], 8001)
    assert redemption["status"] == "used"
    assert redemption["was_new_user"] is True
    stats = (await backend.client.get(f"/api/v1/admin/promo-codes/{promo['id']}/stats")).json()
    assert stats["used"] == 1 and stats["used_new_users"] == 1 and stats["used_existing_users"] == 0
    assert stats["revenue"] == pytest.approx(quote["final_amount"])
    assert stats["discount_total"] == pytest.approx(discount)
    listed = (await backend.client.get("/api/v1/admin/promo-codes")).json()["promo_codes"]
    assert [(p["code"], p["used"]) for p in listed] == [("SVOI30", 1)]


async def test_code_cannot_be_used_twice_by_one_user(backend):
    promo = await _create_code(backend, audience="all")
    await register_user(backend, 8002)
    invoice = (await _invoice(backend, 8002)).json()
    await _pay(backend, invoice["payment_id"])

    quote = await _quote(backend, 8002)
    assert quote["promo_status"] == "already_used"
    assert quote["final_amount"] == PRICE[STANDARD_TARIFF_ID]
    assert (await _invoice(backend, 8002, number_of_months=2)).status_code == 409
    assert (await _redemption(backend, promo["id"], 8002))["status"] == "used"


async def test_first_purchase_code_refused_to_paying_user(backend):
    await _create_code(backend)
    await register_user(backend, 8003)
    invoice = (await _invoice(backend, 8003, code=None)).json()
    await _pay(backend, invoice["payment_id"])

    quote = await _quote(backend, 8003)
    assert quote["promo_status"] == "not_new_user"
    assert quote["promo_message"]
    assert quote["final_amount"] == PRICE[STANDARD_TARIFF_ID]
    resp = await _invoice(backend, 8003, number_of_months=2)
    assert resp.status_code == 409
    assert resp.json()["detail"] == quote["promo_message"]


async def test_discount_only_for_first_months(backend):
    await _create_code(backend, max_months=1)
    await register_user(backend, 8004)

    one_month = await _quote(backend, 8004)
    three_months = await _quote(backend, 8004, number_of_months=3)

    assert three_months["promo_discount_amount"] == one_month["promo_discount_amount"]
    assert three_months["final_amount"] > one_month["final_amount"] * 2


async def test_code_for_purchase_does_not_apply_to_renewal(backend):
    await _create_code(backend, operations="create_key")
    key = await issue_trial(backend, 8005)

    quote = await _quote(backend, 8005, operation="renew_key", email=key["email"])

    assert quote["promo_status"] == "wrong_operation"
    assert quote["final_amount"] == PRICE[STANDARD_TARIFF_ID]


@pytest.mark.parametrize(
    "starts_in, ends_in, reason",
    [(timedelta(hours=1), timedelta(days=1), "not_started"), (-timedelta(days=2), -timedelta(days=1), "expired")],
)
async def test_code_works_only_inside_its_dates(backend, starts_in, ends_in, reason):
    now = datetime.now(timezone.utc)
    await _create_code(backend, starts_at=(now + starts_in).isoformat(), ends_at=(now + ends_in).isoformat())
    await register_user(backend, 8006)

    assert (await _quote(backend, 8006))["promo_status"] == reason
    assert (await _invoice(backend, 8006)).status_code == 409


async def test_disabled_code_stops_working(backend):
    promo = await _create_code(backend)
    await register_user(backend, 8007)

    resp = await backend.client.patch(
        f"/api/v1/admin/promo-codes/{promo['id']}", json={"is_active": False}, headers=_admin_headers()
    )

    assert resp.status_code == 200 and resp.json()["is_active"] is False
    assert (await _quote(backend, 8007))["promo_status"] == "inactive"


async def test_unknown_code_keeps_full_price(backend):
    await register_user(backend, 8008)

    quote = await _quote(backend, 8008, code="NOPE-1234")

    assert quote["promo_status"] == "not_found"
    assert quote["final_amount"] == PRICE[STANDARD_TARIFF_ID]


async def test_without_code_response_has_no_promo(backend):
    await register_user(backend, 8009)

    quote = await _quote(backend, 8009, code=None)

    assert quote["promo_status"] is None
    assert quote["final_amount"] == PRICE[STANDARD_TARIFF_ID]


# --- лимит активаций -----------------------------------------------------------


async def test_limit_counts_reservations_of_other_users(backend):
    promo = await _create_code(backend, max_redemptions=1)
    await register_user(backend, 8010)
    await register_user(backend, 8011)

    assert (await _invoice(backend, 8010)).status_code == 200
    assert (await _quote(backend, 8011))["promo_status"] == "limit_reached"
    assert (await _invoice(backend, 8011)).status_code == 409
    # Свой резерв не мешает: повторное нажатие «оплатить» отдаёт тот же счёт
    assert (await _invoice(backend, 8010)).status_code == 200

    # Резерв истёк (счёт не оплатили) — место освободилось
    await backend.pool.execute(
        "UPDATE promo_redemptions SET reserved_at = NOW() - INTERVAL '1 day' WHERE promo_id = $1", promo["id"]
    )
    assert (await _quote(backend, 8011))["promo_status"] == "applied"


async def test_parallel_payments_do_not_exceed_limit(backend):
    promo = await _create_code(backend, max_redemptions=1)
    users = list(range(8020, 8026))
    for tg_id in users:
        await register_user(backend, tg_id)

    responses = await asyncio.gather(*(_invoice(backend, tg_id) for tg_id in users))

    assert sorted(r.status_code for r in responses) == [200] + [409] * (len(users) - 1)
    reserved = await backend.pool.fetchval("SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = $1", promo["id"])
    assert reserved == 1


# --- сложение со скидками ------------------------------------------------------


async def test_referral_discount_wins_when_bigger(backend):
    await _create_code(backend, code="SMALL5", discount_percent=5)
    await register_user(backend, 8030)
    await register_user(backend, 8031)
    await backend.pool.execute("UPDATE users SET referral_id = 8030 WHERE tg_id = 8031")
    await backend.reload_cache()

    without_code = await _quote(backend, 8031, code=None)
    with_code = await _quote(backend, 8031, code="SMALL5")

    assert without_code["has_referral_discount"] is True
    assert with_code["promo_status"] == "not_better"
    assert with_code["final_amount"] == without_code["final_amount"]
    resp = await _invoice(backend, 8031, code="SMALL5")
    assert resp.status_code == 200
    assert (await db_payment(backend, resp.json()["payment_id"]))["promo_code_id"] is None


async def test_promo_replaces_smaller_referral_discount(backend):
    await _create_code(backend)
    await register_user(backend, 8032)
    await register_user(backend, 8033)
    await backend.pool.execute("UPDATE users SET referral_id = 8032 WHERE tg_id = 8033")
    await backend.reload_cache()

    quote = await _quote(backend, 8033)

    assert quote["promo_status"] == "applied"
    assert quote["has_referral_discount"] is False
    assert quote["final_amount"] == round(PRICE[STANDARD_TARIFF_ID] * 0.7, 2)


# --- защита ---------------------------------------------------------------------


async def test_guessing_codes_is_blocked_per_user(backend, monkeypatch):
    from app import factories

    monkeypatch.setattr(factories.promo_failed_attempts(), "_max", 3)
    await _create_code(backend)
    await register_user(backend, 8040)

    for i in range(3):
        assert (await _quote(backend, 8040, code=f"GUESS{i:03d}"))["promo_status"] == "not_found"

    assert (await _quote(backend, 8040))["promo_status"] == "too_many_attempts"


async def test_personal_code_is_invisible_to_others(backend):
    now = datetime.now(timezone.utc)
    await backend.pool.execute(
        "INSERT INTO promo_codes (code, discount_percent, starts_at, ends_at, audience, owner_tg_id)"
        " VALUES ('VERNI-7K2Q', 30, $1, $2, 'all', 8050)",
        now - timedelta(hours=1),
        now + timedelta(days=1),
    )
    await register_user(backend, 8050)
    await register_user(backend, 8051)

    assert (await _quote(backend, 8051, code="VERNI-7K2Q"))["promo_status"] == "not_found"
    assert (await _quote(backend, 8050, code="VERNI-7K2Q"))["promo_status"] == "applied"
    listed = (await backend.client.get("/api/v1/admin/promo-codes")).json()["promo_codes"]
    assert listed == []


async def test_bot_secret_cannot_create_codes(backend):
    now = datetime.now(timezone.utc)
    resp = await backend.client.post(
        "/api/v1/admin/promo-codes",
        json={
            "code": "HACK1234",
            "discount_percent": 90,
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(days=1)).isoformat(),
        },
    )

    assert resp.status_code in (401, 403)
    assert await backend.pool.fetchval("SELECT COUNT(*) FROM promo_codes") == 0


@pytest.mark.parametrize(
    "overrides",
    [{"code": "bad code!"}, {"discount_percent": 95}, {"ends_at": "2020-01-01T00:00:00+00:00"}],
)
async def test_invalid_code_params_rejected(backend, overrides):
    now = datetime.now(timezone.utc)
    body = {
        "code": "GOOD-CODE",
        "discount_percent": 30,
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(days=1)).isoformat(),
        **overrides,
    }

    resp = await backend.client.post("/api/v1/admin/promo-codes", json=body, headers=_admin_headers())

    assert resp.status_code == 422
    assert await backend.pool.fetchval("SELECT COUNT(*) FROM promo_codes") == 0


async def test_duplicate_code_rejected(backend):
    await _create_code(backend, code="SAME-CODE")

    resp = await backend.client.post(
        "/api/v1/admin/promo-codes",
        json={
            "code": "same-code",
            "discount_percent": 10,
            "starts_at": datetime.now(timezone.utc).isoformat(),
            "ends_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
        headers=_admin_headers(),
    )

    assert resp.status_code == 409


async def test_family_tariff_discount_uses_its_price(backend):
    await _create_code(backend)
    await register_user(backend, 8060)

    quote = await _quote(backend, 8060, tariff_id=FAMILY_TARIFF_ID)

    assert quote["promo_discount_amount"] == round(PRICE[FAMILY_TARIFF_ID] * 0.30, 2)
