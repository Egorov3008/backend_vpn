"""Платежи (/api/v1/payments) на живой БД и заглушках ЮKassa, 3x-UI и Telegram.

Путь как в проде: счёт создаётся в ЮKassa, оплата меняет статус там, вебхук приходит
в backend, backend сверяет статус с ЮKassa и выдаёт или продлевает ключ.
"""
import asyncio
import time

import pytest

from tests.integration.conftest import FAMILY_TARIFF_ID, PAID_INBOUNDS, STANDARD_TARIFF_ID
from tests.integration.helpers import db_key, db_keys_of, db_payment, db_user, issue_trial, register_user

DAY_MS = 24 * 3600 * 1000


async def _create(backend, tg_id, tariff_id=STANDARD_TARIFF_ID, **body):
    return await backend.client.post(
        "/api/v1/payments/create", json={"tg_id": tg_id, "tariff_id": tariff_id, **body}
    )


async def _webhook(backend, payment_id):
    return await backend.client.post(
        "/api/v1/payments/webhook",
        json={"type": "notification", "event": "payment.succeeded", "object": {"id": payment_id}},
    )


async def _pay(backend, payment_id):
    """Пользователь оплатил счёт, ЮKassa прислала вебхук."""
    backend.fakes.set_payment_status(payment_id, "succeeded")
    resp = await _webhook(backend, payment_id)
    assert resp.status_code == 200, resp.text


async def _invoice(backend, tg_id, **body) -> dict:
    resp = await _create(backend, tg_id, **body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- выдача ключа -------------------------------------------------------------


async def test_paid_invoice_issues_one_key_and_notifies(backend):
    await register_user(backend, 2001)
    invoice = await _invoice(backend, 2001)
    assert invoice["amount"] == 199.0

    await _pay(backend, invoice["payment_id"])

    keys = await db_keys_of(backend, 2001)
    assert len(keys) == 1
    key = keys[0]
    assert key["payment_id"] == invoice["payment_id"]
    assert key["tariff_id"] == STANDARD_TARIFF_ID
    assert abs(key["expiry_time"] - (time.time() * 1000 + 30 * DAY_MS)) < 60_000
    panel = backend.fakes.panel_client(key["email"])
    assert panel["client"]["expiryTime"] == key["expiry_time"]
    assert sorted(panel["inboundIds"]) == PAID_INBOUNDS
    assert (await db_payment(backend, invoice["payment_id"]))["status"] == "succeeded"
    assert backend.fakes.sent_messages(2001), "пользователь не получил сообщение о ключе"


ADMIN_TG_ID = 9001


async def test_admin_notified_once_with_service_id_and_amount(backend, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ID", [ADMIN_TG_ID])
    await register_user(backend, 2010)
    invoice = await _invoice(backend, 2010, number_of_months=3)
    yk_description = backend.fakes.state()["yk_requests"][-1]["description"]

    await _pay(backend, invoice["payment_id"])
    await _webhook(backend, invoice["payment_id"])  # повтор вебхука не шлёт второе сообщение

    messages = backend.fakes.sent_messages(ADMIN_TG_ID)
    assert len(messages) == 1
    text = messages[0]["params"]["text"]
    assert yk_description in text
    assert invoice["payment_id"] in text
    assert f"{invoice['amount']:.2f}" in text


async def test_admin_notification_disabled_by_env(backend, monkeypatch):
    import config

    monkeypatch.setattr(config, "ADMIN_ID", [ADMIN_TG_ID])
    monkeypatch.setattr(config.settings, "admin_payment_notify_enabled", False)
    await register_user(backend, 2011)
    invoice = await _invoice(backend, 2011)

    await _pay(backend, invoice["payment_id"])

    assert (await db_payment(backend, invoice["payment_id"]))["status"] == "succeeded"
    assert backend.fakes.sent_messages(ADMIN_TG_ID) == []


async def test_repeated_webhook_does_not_issue_second_key(backend):
    await register_user(backend, 2002)
    invoice = await _invoice(backend, 2002)
    await _pay(backend, invoice["payment_id"])

    again = await _webhook(backend, invoice["payment_id"])

    assert again.status_code == 200
    assert len(await db_keys_of(backend, 2002)) == 1
    assert len(backend.fakes.state()["clients"]) == 1


async def test_concurrent_webhooks_issue_one_key(backend):
    await register_user(backend, 2003)
    invoice = await _invoice(backend, 2003)
    backend.fakes.set_payment_status(invoice["payment_id"], "succeeded")

    await asyncio.gather(*(_webhook(backend, invoice["payment_id"]) for _ in range(5)))

    assert len(await db_keys_of(backend, 2003)) == 1
    assert len(backend.fakes.state()["clients"]) == 1


async def test_webhook_without_confirmed_payment_issues_nothing(backend):
    """Тело вебхука не подписано: ключ выдаётся только по статусу из API ЮKassa."""
    await register_user(backend, 2004)
    invoice = await _invoice(backend, 2004)

    resp = await _webhook(backend, invoice["payment_id"])

    assert resp.status_code == 409
    assert await db_keys_of(backend, 2004) == []
    assert (await db_payment(backend, invoice["payment_id"]))["status"] == "pending"


async def test_paid_renewal_extends_same_key_once(backend):
    trial = await issue_trial(backend, 2005)
    before = (await db_key(backend, trial["email"]))["expiry_time"]
    invoice = await _invoice(backend, 2005, operation="renew_key", email=trial["email"])

    await _pay(backend, invoice["payment_id"])
    await _webhook(backend, invoice["payment_id"])

    keys = await db_keys_of(backend, 2005)
    assert [k["email"] for k in keys] == [trial["email"]]
    after = keys[0]["expiry_time"]
    assert abs(after - (before + 30 * DAY_MS)) < 60_000
    assert backend.fakes.panel_client(trial["email"])["client"]["expiryTime"] == after


async def test_renewal_uses_tariff_chosen_at_checkout(backend):
    trial = await issue_trial(backend, 2006)
    invoice = await _invoice(
        backend, 2006, tariff_id=FAMILY_TARIFF_ID, operation="renew_key", email=trial["email"]
    )

    await _pay(backend, invoice["payment_id"])

    key = await db_key(backend, trial["email"])
    assert key["tariff_id"] == FAMILY_TARIFF_ID
    assert key["limit_ip"] == 3


# --- повторные нажатия «оплатить» ---------------------------------------------


async def test_repeat_click_returns_same_open_invoice(backend):
    await register_user(backend, 2010)
    first = await _invoice(backend, 2010)

    second = await _invoice(backend, 2010)

    assert second["payment_id"] == first["payment_id"]
    assert len(backend.fakes.state()["yk_requests"]) == 1


async def test_concurrent_clicks_create_one_invoice(backend):
    await register_user(backend, 2011)

    responses = await asyncio.gather(*(_create(backend, 2011) for _ in range(5)))

    assert {r.json()["payment_id"] for r in responses} == {responses[0].json()["payment_id"]}
    assert len(backend.fakes.state()["yk_requests"]) == 1


async def test_new_invoice_refused_while_previous_is_paid(backend):
    """Оплата прошла, вебхук ещё не дошёл: второй счёт означал бы двойную оплату."""
    await register_user(backend, 2012)
    first = await _invoice(backend, 2012)
    backend.fakes.set_payment_status(first["payment_id"], "succeeded")

    resp = await _create(backend, 2012)

    assert resp.status_code == 409
    assert len(backend.fakes.state()["yk_requests"]) == 1


async def test_new_invoice_refused_while_previous_is_processed(backend):
    await register_user(backend, 2015)
    first = await _invoice(backend, 2015)
    await backend.pool.execute("UPDATE payments SET status = 'processing' WHERE payment_id = $1", first["payment_id"])
    await backend.reload_cache()

    resp = await _create(backend, 2015)

    assert resp.status_code == 409


async def test_new_invoice_refused_when_previous_status_is_unknown(backend):
    await register_user(backend, 2016)
    first = await _invoice(backend, 2016)
    backend.fakes.set_payment_status(first["payment_id"], "waiting_for_something")

    resp = await _create(backend, 2016)

    assert resp.status_code == 503
    assert len(backend.fakes.state()["yk_requests"]) == 1


async def test_canceled_invoice_is_replaced(backend):
    await register_user(backend, 2013)
    first = await _invoice(backend, 2013)
    backend.fakes.set_payment_status(first["payment_id"], "canceled")

    second = await _invoice(backend, 2013)

    assert second["payment_id"] != first["payment_id"]
    assert (await db_payment(backend, first["payment_id"]))["status"] == "canceled"


async def test_different_tariff_gets_its_own_invoice(backend):
    await register_user(backend, 2014)
    first = await _invoice(backend, 2014)

    other = await _invoice(backend, 2014, tariff_id=FAMILY_TARIFF_ID)

    assert other["payment_id"] != first["payment_id"]
    assert other["amount"] == 349.0


# --- статус -------------------------------------------------------------------


async def test_status_follows_yookassa_cancel(backend):
    await register_user(backend, 2020)
    invoice = await _invoice(backend, 2020)
    url = f"/api/v1/payments/{invoice['payment_id']}/status"

    assert (await backend.client.get(url, params={"tg_id": 2020})).json()["status"] == "pending"

    backend.fakes.set_payment_status(invoice["payment_id"], "canceled")
    # Повторная проверка в ЮKassa не чаще раза в 30 секунд
    from api.v1 import payments

    payments._yk_cancel_checked_at.clear()
    resp = await backend.client.get(url, params={"tg_id": 2020})

    assert resp.json()["status"] == "canceled"
    assert (await db_payment(backend, invoice["payment_id"]))["status"] == "canceled"


async def test_status_never_issues_key(backend):
    await register_user(backend, 2021)
    invoice = await _invoice(backend, 2021)
    backend.fakes.set_payment_status(invoice["payment_id"], "succeeded")

    resp = await backend.client.get(f"/api/v1/payments/{invoice['payment_id']}/status", params={"tg_id": 2021})

    assert resp.status_code == 200
    assert await db_keys_of(backend, 2021) == []


async def test_foreign_payment_status_is_hidden(backend):
    await register_user(backend, 2022)
    await register_user(backend, 2023)
    invoice = await _invoice(backend, 2022)

    resp = await backend.client.get(f"/api/v1/payments/{invoice['payment_id']}/status", params={"tg_id": 2023})

    assert resp.status_code == 403


# --- скидки -------------------------------------------------------------------


async def _make_referral(backend, referrer: int, referred: int) -> None:
    await register_user(backend, referrer)
    await register_user(backend, referred)
    link_id = await backend.pool.fetchval(
        "INSERT INTO referral_links (referrer_tg_id, token) VALUES ($1, $2) RETURNING id", referrer, f"ref{referrer}"
    )
    await backend.pool.execute(
        "INSERT INTO referral_redemptions (referral_link_id, referred_tg_id) VALUES ($1, $2)", link_id, referred
    )
    await backend.pool.execute("UPDATE users SET referral_id = $1 WHERE tg_id = $2", referrer, referred)
    await backend.reload_cache()


async def _quote(backend, tg_id, tariff_id=STANDARD_TARIFF_ID, months=1) -> dict:
    resp = await backend.client.post(
        "/api/v1/payments/quotes", json={"tg_id": tg_id, "tariff_id": tariff_id, "number_of_months": months}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_quote_matches_invoice_amount(backend):
    await _make_referral(backend, 2030, 2031)

    quote = await _quote(backend, 2031)
    invoice = await _invoice(backend, 2031)

    assert quote["final_amount"] < 199.0
    assert invoice["amount"] == quote["final_amount"]
    assert (await db_payment(backend, invoice["payment_id"]))["amount"] == pytest.approx(quote["final_amount"])


async def test_first_referred_payment_rewards_referrer_once(backend):
    await _make_referral(backend, 2032, 2033)
    invoice = await _invoice(backend, 2033)

    await _pay(backend, invoice["payment_id"])

    assert (await db_user(backend, 2032))["balance"] > 0
    assert (await db_user(backend, 2033))["check_referral"] is True
    # Скидка приглашённого — только на первую покупку
    assert (await _quote(backend, 2033))["final_amount"] == 199.0


async def test_balance_pays_part_and_is_spent_after_payment(backend):
    await register_user(backend, 2034)
    await backend.pool.execute("UPDATE users SET balance = 50 WHERE tg_id = 2034")
    await backend.reload_cache()

    invoice = await _invoice(backend, 2034)
    assert invoice["amount"] == 149.0

    await _pay(backend, invoice["payment_id"])

    assert (await db_user(backend, 2034))["balance"] == 0


async def test_stock_discount_reaches_yookassa_invoice(backend):
    await register_user(backend, 2035)
    await backend.pool.execute(
        "INSERT INTO stocks (tg_id, stock_type, value) VALUES (2035, 'percent', 10)"
    )
    await backend.reload_cache()

    quote = await _quote(backend, 2035)
    invoice = await _invoice(backend, 2035)

    assert quote["final_amount"] < 199.0
    assert invoice["amount"] == quote["final_amount"]
    sent = backend.fakes.state()["yk_requests"][0]["amount"]["value"]
    assert float(sent) == quote["final_amount"]


async def test_client_cannot_lower_price(backend):
    await register_user(backend, 2036)

    invoice = await _invoice(backend, 2036, amount=1)

    assert invoice["amount"] == 199.0
    assert backend.fakes.state()["yk_requests"][0]["amount"]["value"] == "199.00"


async def test_repeated_webhook_spends_balance_once(backend):
    await register_user(backend, 2037)
    await backend.pool.execute("UPDATE users SET balance = 70 WHERE tg_id = 2037")
    await backend.reload_cache()
    invoice = await _invoice(backend, 2037)
    await _pay(backend, invoice["payment_id"])

    await _webhook(backend, invoice["payment_id"])

    assert (await db_user(backend, 2037))["balance"] == 0


async def test_referrer_is_rewarded_only_for_first_payment(backend):
    await _make_referral(backend, 2038, 2039)
    first = await _invoice(backend, 2039)
    await _pay(backend, first["payment_id"])
    bonus = (await db_user(backend, 2038))["balance"]

    second = await _invoice(backend, 2039, tariff_id=FAMILY_TARIFF_ID)
    await _pay(backend, second["payment_id"])

    assert (await db_user(backend, 2038))["balance"] == bonus
    rewards = await backend.pool.fetchval("SELECT count(*) FROM referral_rewards WHERE referrer_tg_id = 2038")
    assert rewards == 1


async def test_cabinet_referral_stats_follow_friends_payment(backend):
    from app.core.miniapp_session import web_sessions

    await _make_referral(backend, 2040, 2041)
    await register_user(backend, 2042)
    await backend.pool.execute("UPDATE users SET referral_id = 2040 WHERE tg_id = 2042")
    await backend.reload_cache()
    token, _ = web_sessions.issue(2040)

    async def stats() -> dict:
        resp = await backend.client.get(
            "/api/v1/web/referral-stats", headers={"Cookie": f"web_session={token}"}
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    before = await stats()
    await _pay(backend, (await _invoice(backend, 2041))["payment_id"])
    after = await stats()

    assert (before["invited_count"], before["paid_count"], before["earned_total"]) == (2, 0, 0)
    assert (after["invited_count"], after["paid_count"]) == (2, 1)
    assert after["earned_total"] > 0
    assert after["balance"] == pytest.approx((await db_user(backend, 2040))["balance"])
