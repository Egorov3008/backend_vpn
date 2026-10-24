"""GET /api/v1/admin/stats/month на живой БД: что попадает в статистику месяца и в какой день.

Берём прошлый календарный месяц (M) и позапрошлый (P): оба уже закончились, поэтому
результат не зависит от того, какое сегодня число.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.integration.conftest import FAMILY_TARIFF_ID, STANDARD_TARIFF_ID, TRIAL_TARIFF_ID
from tests.integration.helpers import insert_key

URL = "/api/v1/admin/stats/month"
DAY = timedelta(days=1)


def _months() -> tuple[datetime, datetime, datetime]:
    """Начала позапрошлого (P), прошлого (M) и текущего месяца, UTC."""
    this = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m = (this - DAY).replace(day=1)
    p = (m - DAY).replace(day=1)
    return p, m, this


P, M, THIS = _months()
M_PARAM = M.strftime("%Y-%m")


async def add_user(backend, tg_id: int, created_at: datetime) -> None:
    await backend.pool.execute("INSERT INTO users (tg_id, created_at) VALUES ($1, $2)", tg_id, created_at)


async def add_payment(backend, tg_id: int, amount: float, paid_at: datetime, *, status="succeeded", payment_type=None):
    await backend.pool.execute(
        "INSERT INTO payments (payment_id, tg_id, amount, status, payment_type, created_at, succeeded_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        f"p-{uuid.uuid4().hex[:10]}", tg_id, amount, status, payment_type or f"create_key|{STANDARD_TARIFF_ID}",
        paid_at, paid_at if status == "succeeded" else None,
    )


async def add_site_key(backend, tg_id: int, created_at: datetime, converted_tg_id=None) -> str:
    """Ключ, выданный гостю сайта (landing_uid), созданный в момент created_at."""
    email = await insert_key(
        backend, tg_id, tariff_id=TRIAL_TARIFF_ID, expiry_time=int(time.time() * 1000) + 3_600_000, in_panel=False
    )
    await backend.pool.execute(
        "UPDATE keys SET landing_uid = $2, created_at = $3, converted_tg_id = $4 WHERE email = $1",
        email, uuid.uuid4().hex[:16], int(created_at.timestamp() * 1000), converted_tg_id,
    )
    return email


async def get_month(backend, **params) -> dict:
    resp = await backend.client.get(URL, params={"month": M_PARAM, **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_payments_compared_with_previous_month(backend):
    await add_payment(backend, 1, 199, M + 2 * DAY + timedelta(hours=10))
    await add_payment(backend, 2, 349, M + 9 * DAY, payment_type="renew_key|x@y")
    await add_payment(backend, 1, 999, M + 9 * DAY, status="pending")
    await add_payment(backend, 1, 999, M + 9 * DAY, status="canceled")
    await add_payment(backend, 3, 100, P + 4 * DAY)
    await add_payment(backend, 3, 777, THIS + timedelta(hours=1))  # следующий месяц

    body = await get_month(backend)

    pay = body["payments"]
    assert (pay["current"]["revenue"], pay["current"]["count"], pay["current"]["paying_users"]) == (548, 2, 2)
    assert pay["previous_full"]["revenue"] == pytest.approx(100)
    assert len(pay["daily_revenue"]) == body["days_in_month"] == body["days_elapsed"]
    assert pay["daily_revenue"][2] == pytest.approx(199)
    assert sum(pay["daily_revenue"]) == pytest.approx(548)
    assert pay["daily_revenue_prev"][4] == pytest.approx(100)
    assert body["prev_month"] == P.strftime("%Y-%m")


async def test_day_boundaries_follow_admin_timezone(backend):
    # За 90 минут до начала M по UTC — это уже 1-е число M по Москве (UTC+3).
    paid_at = M - timedelta(minutes=90)
    await add_payment(backend, 1, 199, paid_at)

    utc = await get_month(backend)
    msk = await get_month(backend, tz="Europe/Moscow")

    assert utc["payments"]["current"]["revenue"] == 0
    assert utc["payments"]["daily_revenue_prev"][-1] == pytest.approx(199)
    assert msk["payments"]["current"]["revenue"] == pytest.approx(199)
    assert msk["payments"]["daily_revenue"][0] == pytest.approx(199)


async def test_new_users_and_their_payments(backend):
    for tg_id in (1, 2, 3):
        await add_user(backend, tg_id, M + 5 * DAY)
    await add_user(backend, -5, M + 5 * DAY)  # анонимный гость сайта — не Telegram-пользователь
    await add_user(backend, 4, P + 5 * DAY)  # зарегистрирован раньше, платит в M
    await add_payment(backend, 1, 199, M + 6 * DAY)
    await add_payment(backend, 1, 199, M + 20 * DAY)
    await add_payment(backend, 4, 199, M + 6 * DAY)

    new = (await get_month(backend))["new_users"]

    assert new["current"] == {"count": 3, "paid_users": 1, "payments": 2, "revenue": 398}
    assert new["previous_full"]["count"] == 1
    assert new["daily"][5] == 3


async def test_web_signups_and_what_happened_to_them(backend):
    await add_site_key(backend, -1, M + 3 * DAY)  # гость так и не привязал Telegram
    await add_site_key(backend, 7, M + 3 * DAY, converted_tg_id=7)  # привязал и оплатил
    await add_site_key(backend, 8, M + 4 * DAY, converted_tg_id=8)  # привязал, не платил
    await add_site_key(backend, -2, P + 3 * DAY)
    await add_payment(backend, 7, 199, M + 5 * DAY)
    await add_payment(backend, 8, 199, M + 2 * DAY)  # оплата раньше ключа с сайта — не из-за сайта

    web = (await get_month(backend))["web"]

    assert web["current"] == {"guests": 3, "linked_telegram": 2, "paid": 1}
    assert web["previous_full"]["guests"] == 1
    assert (web["daily"][3], web["daily"][4]) == (2, 1)


async def test_most_popular_tariff_counts_purchases_and_renewals(backend):
    family_key = await insert_key(backend, 9, tariff_id=FAMILY_TARIFF_ID, expiry_time=0, in_panel=False)
    await add_payment(backend, 1, 199, M + DAY, payment_type=f"create_key|{STANDARD_TARIFF_ID}")
    await add_payment(backend, 2, 199, M + DAY, payment_type=f"create_key|{STANDARD_TARIFF_ID}")
    await add_payment(backend, 9, 349, M + DAY, payment_type=f"renew_key|{family_key}")
    await add_payment(backend, 9, 349, P + DAY, payment_type=f"renew_key|{family_key}")

    tariffs = (await get_month(backend))["tariffs"]

    assert [(t["tariff"], t["payments"]) for t in tariffs] == [("Стандарт", 2), ("Семейный", 1)]


async def test_activity_counts_only_unexpired_keys(backend):
    now_ms = int(time.time() * 1000)
    await insert_key(backend, 1, tariff_id=STANDARD_TARIFF_ID, expiry_time=now_ms + 86_400_000)
    await insert_key(backend, 1, tariff_id=TRIAL_TARIFF_ID, expiry_time=now_ms + 86_400_000)
    await insert_key(backend, 2, tariff_id=STANDARD_TARIFF_ID, expiry_time=now_ms - 1000)
    await add_site_key(backend, -3, datetime.now(timezone.utc))

    activity = (await get_month(backend))["activity"]

    assert activity == {"active_users": 1, "active_keys": 3, "active_keys_paid": 1, "active_keys_guest": 1}


async def test_current_month_by_default(backend):
    body = (await backend.client.get(URL)).json()
    assert body["month"] == THIS.strftime("%Y-%m")
    assert len(body["payments"]["daily_revenue"]) == body["days_elapsed"] <= body["days_in_month"]


@pytest.mark.parametrize("params", [{"month": "2026-13"}, {"month": "oct"}, {"month": "2999-01"}, {"tz": "Mars/Olympus"}])
async def test_rejects_bad_period(backend, params):
    resp = await backend.client.get(URL, params=params)
    assert resp.status_code == 422


async def test_requires_admin_or_bot_auth(backend):
    resp = await backend.client.get(URL, headers={"X-Bot-Secret": "wrong"})
    assert resp.status_code in (401, 403)
