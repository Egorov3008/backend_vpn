"""Лендинг (/api/v1/landing) на живой БД и заглушке 3x-UI.

Анонимный 24-часовой ключ на лендинг-inbound, его состояние по подписанной куке и
привязка к Telegram-аккаунту ботом (claim для нового пользователя, mark-converted
для существующего), включая привязку реферера.
"""
import asyncio
import time

from tests.integration.conftest import LANDING_INTERNAL_SECRET, PAID_INBOUNDS, TRIAL_TARIFF_ID
from tests.integration.helpers import db_key, db_user, register_user

HOUR_MS = 3600 * 1000
DAY_MS = 24 * HOUR_MS
BOT_HEADERS = {"X-Landing-Internal-Secret": LANDING_INTERNAL_SECRET}


async def _quick_key(backend) -> tuple[dict, str]:
    """Гость получил ключ; возвращает ответ и куку tg_landing_id."""
    resp = await backend.client.post("/api/v1/landing/quick-key")
    assert resp.status_code == 200, resp.text
    return resp.json(), resp.cookies["tg_landing_id"]


async def _landing_email(backend, landing_uid: str) -> str:
    return await backend.pool.fetchval("SELECT email FROM keys WHERE landing_uid = $1", landing_uid)


async def _state(backend, cookie: str) -> dict:
    resp = await backend.client.get("/api/v1/landing/state", headers={"Cookie": f"tg_landing_id={cookie}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _claim(backend, landing_uid: str, tg_id: int, **body):
    return await backend.client.post(
        f"/api/v1/landing/claim/{landing_uid}", json={"tg_id": tg_id, **body}, headers=BOT_HEADERS
    )


async def _mark_converted(backend, landing_uid: str, tg_id: int, **body):
    return await backend.client.post(
        f"/api/v1/landing/mark-converted/{landing_uid}", json={"tg_id": tg_id, **body}, headers=BOT_HEADERS
    )


async def _referral_link(backend, referrer: int) -> str:
    token = f"ref_{referrer:012x}"
    await backend.pool.execute("INSERT INTO referral_links (referrer_tg_id, token) VALUES ($1, $2)", referrer, token)
    return token


# --- гостевой ключ ------------------------------------------------------------


async def test_quick_key_lives_24h_on_available_inbounds(backend):
    data, cookie = await _quick_key(backend)

    email = await _landing_email(backend, data["landing_uid"])
    row = await db_key(backend, email)
    assert row["tg_id"] < 0
    assert abs(row["expiry_time"] - (time.time() * 1000 + DAY_MS)) < 60_000
    panel = backend.fakes.panel_client(email)
    assert sorted(panel["inboundIds"]) == PAID_INBOUNDS
    # Панель и БД получают срок отдельными вызовами — расхождение в доли секунды
    assert abs(panel["client"]["expiryTime"] - row["expiry_time"]) < 60_000

    state = await _state(backend, cookie)
    assert state["state"] == "active"
    assert state["landing_uid"] == data["landing_uid"]
    assert state["key_value"] == data["key_value"]
    assert state["already_registered"] is False


async def test_state_without_valid_cookie_is_new(backend):
    _, cookie = await _quick_key(backend)

    assert (await backend.client.get("/api/v1/landing/state")).json()["state"] == "new"
    assert (await _state(backend, cookie + "x"))["state"] == "new"


async def test_state_reports_expiring_and_expired(backend):
    data, cookie = await _quick_key(backend)
    now_ms = int(time.time() * 1000)

    await backend.pool.execute(
        "UPDATE keys SET expiry_time = $1 WHERE landing_uid = $2", now_ms + 2 * HOUR_MS, data["landing_uid"]
    )
    await backend.reload_cache()
    assert (await _state(backend, cookie))["state"] == "expiring"

    await backend.pool.execute("UPDATE keys SET expiry_time = $1 WHERE landing_uid = $2", now_ms - 1, data["landing_uid"])
    await backend.reload_cache()
    assert (await _state(backend, cookie))["state"] == "expired"


# --- привязка нового пользователя (claim) -------------------------------------


async def test_claim_moves_key_to_user_and_extends_to_trial(backend):
    data, cookie = await _quick_key(backend)
    email = await _landing_email(backend, data["landing_uid"])
    await register_user(backend, 3001)

    resp = await _claim(backend, data["landing_uid"], 3001)

    assert resp.json()["status"] == "claimed"
    row = await db_key(backend, email)
    assert row["tg_id"] == 3001
    assert row["converted_tg_id"] == 3001
    assert row["tariff_id"] == TRIAL_TARIFF_ID
    assert row["key"] == data["key_value"], "ссылка в приложении должна остаться прежней"
    assert abs(row["expiry_time"] - (time.time() * 1000 + 7 * DAY_MS)) < 60_000
    panel = backend.fakes.panel_client(email)
    assert set(PAID_INBOUNDS) <= set(panel["inboundIds"])
    assert panel["client"]["expiryTime"] == row["expiry_time"]
    assert (await db_user(backend, 3001))["trial"] == 1
    assert (await _state(backend, cookie))["state"] == "converted"


async def test_repeated_claim_by_same_user_changes_nothing(backend):
    data, _ = await _quick_key(backend)
    await register_user(backend, 3002)
    first = (await _claim(backend, data["landing_uid"], 3002)).json()

    again = (await _claim(backend, data["landing_uid"], 3002)).json()

    assert again["status"] == "already_claimed"
    assert again["expires_at_ms"] == first["expires_at_ms"]


async def test_claimed_key_cannot_be_taken_by_another_user(backend):
    data, _ = await _quick_key(backend)
    email = await _landing_email(backend, data["landing_uid"])
    await register_user(backend, 3003)
    await register_user(backend, 3004)
    await _claim(backend, data["landing_uid"], 3003)

    resp = await _claim(backend, data["landing_uid"], 3004)

    assert resp.json()["status"] == "already_claimed_other"
    assert (await db_key(backend, email))["tg_id"] == 3003


async def test_claim_panel_failure_can_be_retried(backend):
    data, _ = await _quick_key(backend)
    email = await _landing_email(backend, data["landing_uid"])
    await register_user(backend, 3005)
    backend.fakes.fail_panel("clients/update", "clients/" + email + "/attach", "clients/bulkAttach")

    failed = await _claim(backend, data["landing_uid"], 3005)

    assert failed.status_code == 500
    row = await db_key(backend, email)
    assert row["converted_tg_id"] is None
    assert row["tg_id"] < 0

    backend.fakes.fail_panel()
    retried = await _claim(backend, data["landing_uid"], 3005)
    assert retried.json()["status"] == "claimed"


async def test_claim_requires_internal_secret(backend):
    data, _ = await _quick_key(backend)
    await register_user(backend, 3006)

    resp = await backend.client.post(f"/api/v1/landing/claim/{data['landing_uid']}", json={"tg_id": 3006})

    assert resp.status_code == 401
    assert (await db_key(backend, await _landing_email(backend, data["landing_uid"])))["converted_tg_id"] is None


# --- существующий пользователь (mark-converted) -------------------------------


async def test_existing_user_keeps_landing_key_until_it_expires(backend):
    data, cookie = await _quick_key(backend)
    email = await _landing_email(backend, data["landing_uid"])
    await register_user(backend, 3010)

    resp = await _mark_converted(backend, data["landing_uid"], 3010)

    assert resp.json()["ok"] is True
    row = await db_key(backend, email)
    assert row["converted_tg_id"] == 3010
    assert row["tg_id"] < 0, "ключ не переносится на существующий аккаунт"
    state = await _state(backend, cookie)
    assert state["state"] == "active"
    assert state["already_registered"] is True


# --- реферер ------------------------------------------------------------------


async def test_claim_with_ref_token_attaches_referrer(backend):
    await register_user(backend, 3020)
    token = await _referral_link(backend, 3020)
    await backend.reload_cache()
    data, _ = await _quick_key(backend)
    await register_user(backend, 3021)

    resp = await _claim(backend, data["landing_uid"], 3021, ref_token=token)

    assert resp.json()["referrer_tg_id"] == 3020
    assert (await db_user(backend, 3021))["referral_id"] == 3020
    redemptions = await backend.pool.fetchval(
        "SELECT count(*) FROM referral_redemptions WHERE referred_tg_id = 3021"
    )
    assert redemptions == 1


async def test_self_referral_is_ignored(backend):
    await register_user(backend, 3022)
    token = await _referral_link(backend, 3022)
    await backend.reload_cache()
    data, _ = await _quick_key(backend)

    await _mark_converted(backend, data["landing_uid"], 3022, ref_token=token)

    assert (await db_user(backend, 3022))["referral_id"] is None


async def test_existing_referrer_is_not_overwritten(backend):
    await register_user(backend, 3023)
    await register_user(backend, 3024)
    await register_user(backend, 3025)
    await backend.pool.execute("UPDATE users SET referral_id = 3023 WHERE tg_id = 3025")
    token = await _referral_link(backend, 3024)
    await backend.reload_cache()
    data, _ = await _quick_key(backend)

    await _mark_converted(backend, data["landing_uid"], 3025, ref_token=token)

    assert (await db_user(backend, 3025))["referral_id"] == 3023


async def test_concurrent_attach_keeps_one_referrer(backend):
    from api.v1.landing import _attach_referral_if_any

    for tg_id in (3030, 3031, 3032):
        await register_user(backend, tg_id)
    tokens = [await _referral_link(backend, 3030), await _referral_link(backend, 3031)]
    await backend.reload_cache()
    sd, cache = backend.app.state.service_data, backend.app.state.cache

    await asyncio.gather(
        *(_attach_referral_if_any(backend.pool, sd, cache, None, 3032, raw_ref_token=t) for t in tokens)
    )

    referral_id = (await db_user(backend, 3032))["referral_id"]
    assert referral_id in (3030, 3031)
    rows = await backend.pool.fetch("SELECT referral_link_id FROM referral_redemptions WHERE referred_tg_id = 3032")
    assert len(rows) == 1
    link_owner = await backend.pool.fetchval(
        "SELECT referrer_tg_id FROM referral_links WHERE id = $1", rows[0]["referral_link_id"]
    )
    assert link_owner == referral_id


async def test_ref_cookie_wins_over_token_in_body(backend):
    for tg_id in (3033, 3034):
        await register_user(backend, tg_id)
    cookie_token = await _referral_link(backend, 3033)
    body_token = await _referral_link(backend, 3034)
    await backend.reload_cache()
    resp = await backend.client.post("/api/v1/landing/set-ref-cookie", json={"ref_token": cookie_token})
    tg_ref = resp.cookies["tg_ref"]
    data, _ = await _quick_key(backend)
    await register_user(backend, 3035)

    await backend.client.post(
        f"/api/v1/landing/claim/{data['landing_uid']}",
        json={"tg_id": 3035, "ref_token": body_token},
        headers={**BOT_HEADERS, "Cookie": f"tg_ref={tg_ref}"},
    )

    assert (await db_user(backend, 3035))["referral_id"] == 3033
