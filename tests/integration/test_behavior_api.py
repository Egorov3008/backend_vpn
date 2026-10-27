"""Журнал действий и отчёт «Поведение» (GET /admin/behavior, /admin/users/{tg_id}/events) на живой БД."""
import time
import uuid
from datetime import datetime, timedelta, timezone

from tests.integration.conftest import STANDARD_TARIFF_ID, TRIAL_TARIFF_ID
from tests.integration.helpers import insert_key, register_user

WEB_HEADERS = {"X-Requested-With": "web"}
DAY_MS = 24 * 3600 * 1000


def _web(tg_id: int) -> dict:
    from app.core.miniapp_session import web_sessions

    token, _ = web_sessions.issue(tg_id)
    return {**WEB_HEADERS, "Cookie": f"web_session={token}"}


def _miniapp(tg_id: int) -> dict:
    from app.core.miniapp_session import issue_session_token

    token, _ = issue_session_token(tg_id)
    return {"Authorization": f"Bearer {token}"}


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _events(backend, tg_id: int) -> list[dict]:
    resp = await backend.client.get(f"/api/v1/admin/users/{tg_id}/events")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _behavior(backend, **params) -> dict:
    resp = await backend.client.get("/api/v1/admin/behavior", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _add_payment(backend, tg_id: int, amount: float, at: datetime, *, status="succeeded", payment_type=None):
    await backend.pool.execute(
        "INSERT INTO payments (payment_id, tg_id, amount, status, payment_type, created_at, succeeded_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        f"p-{uuid.uuid4().hex[:10]}", tg_id, amount, status, payment_type or f"create_key|{STANDARD_TARIFF_ID}",
        at, at if status == "succeeded" else None,
    )


# --- журнал -------------------------------------------------------------------


async def test_cabinet_actions_land_in_user_timeline(backend):
    await register_user(backend, 7201)
    headers = _web(7201)

    assert (await backend.client.get("/api/v1/web/me", headers=headers)).status_code == 200
    assert (await backend.client.post("/api/v1/web/keys/trial", headers=headers)).status_code == 200
    invoice = await backend.client.post(
        "/api/v1/web/payments", json={"tariff_id": STANDARD_TARIFF_ID, "number_of_months": 1}, headers=headers
    )
    assert invoice.status_code == 200, invoice.text
    payment_id = invoice.json()["payment_id"]
    backend.fakes.set_payment_status(payment_id, "succeeded")
    await backend.client.post(
        "/api/v1/payments/webhook",
        json={"type": "notification", "event": "payment.succeeded", "object": {"id": payment_id}},
    )

    events = await _events(backend, 7201)

    assert [(e["name"], e["source"]) for e in events] == [
        ("payment_succeeded", "backend"),
        ("payment_created", "web"),
        ("trial_created", "web"),
        ("app_opened", "web"),
    ]
    created = events[1]
    assert created["props"]["tariff_id"] == STANDARD_TARIFF_ID
    assert created["props"]["amount"] == invoice.json()["amount"]
    assert all(e["title"] and e["title"] != e["name"] for e in events)


async def test_miniapp_open_is_logged_with_its_source(backend):
    await register_user(backend, 7202)

    resp = await backend.client.get("/api/v1/miniapp/me", headers=_miniapp(7202))

    assert resp.status_code == 200, resp.text
    assert [(e["name"], e["source"]) for e in await _events(backend, 7202)] == [("app_opened", "miniapp")]


async def test_timeline_is_per_user(backend):
    await register_user(backend, 7203)
    await register_user(backend, 7204)

    await backend.client.get("/api/v1/web/me", headers=_web(7203))

    assert await _events(backend, 7204) == []


async def test_old_events_are_purged(backend):
    from services.user_events import UserEventRepository

    await backend.pool.execute(
        "INSERT INTO user_events (tg_id, name, source, created_at) VALUES"
        " (7205, 'app_opened', 'web', NOW() - INTERVAL '200 days'),"
        " (7205, 'app_opened', 'web', NOW() - INTERVAL '1 day')"
    )

    deleted = await UserEventRepository(backend.pool).purge_before(datetime.now(timezone.utc) - timedelta(days=180))

    assert deleted == 1
    assert len(await _events(backend, 7205)) == 1


# --- отчёт --------------------------------------------------------------------


async def test_new_users_funnel(backend):
    now = datetime.now(timezone.utc)
    for tg_id in (7301, 7302, 7303):
        await register_user(backend, tg_id)
    connected = await insert_key(backend, 7301, tariff_id=TRIAL_TARIFF_ID, expiry_time=_now_ms() + DAY_MS, in_panel=False)
    await backend.pool.execute("UPDATE keys SET used_traffic = 1024 WHERE email = $1", connected)
    await insert_key(backend, 7302, tariff_id=TRIAL_TARIFF_ID, expiry_time=_now_ms() + DAY_MS, in_panel=False)
    await _add_payment(backend, 7301, 150, now)
    # Зарегистрирован давно — в воронку окна не попадает.
    await backend.pool.execute("INSERT INTO users (tg_id, created_at) VALUES (7304, $1)", now - timedelta(days=90))

    report = await _behavior(backend, days=30)

    assert report["funnel"] == {"registered": 3, "got_key": 2, "connected": 1, "paid": 1}


async def test_abandoned_payments(backend):
    now = datetime.now(timezone.utc)
    await _add_payment(backend, 7311, 150, now - timedelta(days=2), status="pending")
    # Бросил счёт, но потом оплатил другой — не брошенный.
    await _add_payment(backend, 7312, 150, now - timedelta(days=2), status="canceled")
    await _add_payment(backend, 7312, 150, now - timedelta(days=1))

    abandoned = (await _behavior(backend, days=30))["abandoned"]

    assert abandoned == {"created": 3, "count": 1, "users": 1, "amount": 150.0}


async def test_churn_and_keys_at_risk(backend):
    now = datetime.now(timezone.utc)
    await register_user(backend, 7321)
    await register_user(backend, 7322)
    await register_user(backend, 7323)
    for tg_id in (7321, 7322, 7323):
        await _add_payment(backend, tg_id, 150, now - timedelta(days=40))
    await insert_key(backend, 7321, tariff_id=STANDARD_TARIFF_ID, expiry_time=_now_ms() - 2 * DAY_MS, in_panel=False)
    expiring = await insert_key(backend, 7322, tariff_id=STANDARD_TARIFF_ID, expiry_time=_now_ms() + DAY_MS, in_panel=False)
    # Скоро истекает, но есть второй ключ дольше — не в зоне риска.
    await insert_key(backend, 7323, tariff_id=STANDARD_TARIFF_ID, expiry_time=_now_ms() + DAY_MS, in_panel=False)
    await insert_key(backend, 7323, tariff_id=STANDARD_TARIFF_ID, expiry_time=_now_ms() + 30 * DAY_MS, in_panel=False)
    await _add_payment(backend, 7323, 150, now - timedelta(days=3), payment_type="renew_key|x")

    churn = (await _behavior(backend, days=30))["churn"]

    assert churn["left_users"] == 1
    assert churn["active_paying"] == 2
    assert churn["renewals"] == 1
    assert [k["email"] for k in churn["at_risk"]] == [expiring]


async def test_trial_without_traffic_counts_as_not_connected(backend):
    await register_user(backend, 7331)
    await register_user(backend, 7332)
    await insert_key(backend, 7331, tariff_id=TRIAL_TARIFF_ID, expiry_time=_now_ms() + DAY_MS, in_panel=False)
    used = await insert_key(backend, 7332, tariff_id=TRIAL_TARIFF_ID, expiry_time=_now_ms() + DAY_MS, in_panel=False)
    await backend.pool.execute("UPDATE keys SET used_traffic = 10 WHERE email = $1", used)

    assert (await _behavior(backend, days=7))["churn"]["not_connected"] == 1


async def test_cohort_keeps_user_on_long_tariff(backend):
    now = datetime.now(timezone.utc)
    index = now.year * 12 + now.month - 1 - 2
    two_months_ago = datetime(index // 12, index % 12 + 1, 2, 12, tzinfo=timezone.utc)
    for tg_id in (7341, 7342):
        await register_user(backend, tg_id)
        await _add_payment(backend, tg_id, 450, two_months_ago)
    # 7341 купил на полгода — ключ ещё действует; 7342 взял месяц и не продлил:
    # к началу следующего месяца ключ у него ещё был, к началу текущего — нет.
    await insert_key(backend, 7341, tariff_id=STANDARD_TARIFF_ID, expiry_time=_now_ms() + 90 * DAY_MS, in_panel=False)
    gone = int((two_months_ago + timedelta(days=31)).timestamp() * 1000)
    await insert_key(backend, 7342, tariff_id=STANDARD_TARIFF_ID, expiry_time=gone, in_panel=False)

    cohorts = (await _behavior(backend, days=30, tz="UTC"))["cohorts"]

    row = next(c for c in cohorts if c["month"] == two_months_ago.strftime("%Y-%m"))
    assert row["size"] == 2
    assert row["retained"] == [2, 1]


async def test_activity_counts_people_not_opens(backend):
    for tg_id in (7351, 7352):
        await register_user(backend, tg_id)
    await backend.client.get("/api/v1/web/me", headers=_web(7351))
    await backend.client.get("/api/v1/web/me", headers=_web(7351))
    await backend.client.get("/api/v1/miniapp/me", headers=_miniapp(7352))

    activity = (await _behavior(backend, days=7, tz="UTC"))["activity"]

    assert (activity["users"], activity["web_users"], activity["miniapp_users"]) == (2, 1, 1)
    today = activity["daily"][-1]
    assert (today["web"], today["miniapp"]) == (1, 1)
    assert len(activity["daily"]) == 7
    assert activity["since"] is not None


async def test_behavior_rejects_bad_window(backend):
    assert (await backend.client.get("/api/v1/admin/behavior", params={"days": 0})).status_code == 422
    assert (await backend.client.get("/api/v1/admin/behavior", params={"tz": "Mars/Base"})).status_code == 422


async def test_behavior_requires_admin_auth(backend):
    resp = await backend.client.get("/api/v1/admin/behavior", headers={"X-Bot-Secret": "wrong"})

    assert resp.status_code in (401, 403)
