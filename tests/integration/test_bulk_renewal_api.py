"""Массовое продление действующих платных ключей: preview, запуск, прогресс, отмена.

Проверяем результат (строки в БД, клиентов в панели), а не вызовы моков.
Внешние сервисы — заглушки стенда (tests/integration/conftest.py).
"""
import asyncio
import time

from tests.integration.conftest import FAMILY_TARIFF_ID, STANDARD_TARIFF_ID
from tests.integration.helpers import db_key, insert_key, issue_trial, register_user

ADMIN_TG_ID = 7001
DAY_MS = 86_400_000


def _admin_headers() -> dict:
    from config import settings

    return {"X-API-Key": settings.admin_api_key, "X-Admin-Tg-Id": str(ADMIN_TG_ID)}


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _preview(backend, days: int = 3) -> dict:
    resp = await backend.client.post(
        "/api/v1/admin/keys/bulk-renewals/preview", json={"days": days}, headers=_admin_headers()
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _start(backend, **body):
    return await backend.client.post(
        "/api/v1/admin/keys/bulk-renewals", json=body, headers=_admin_headers()
    )


async def _status(backend, job_id: str) -> dict:
    resp = await backend.client.get(
        f"/api/v1/admin/keys/bulk-renewals/{job_id}", headers=_admin_headers()
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _finished(backend, job_id: str) -> dict:
    from app.factories import bulk_renewal_jobs

    await bulk_renewal_jobs().wait(job_id)
    return await _status(backend, job_id)


async def _seed_mixed_keys(backend) -> dict:
    """Один активный платный (7), ещё один платный на другом тарифе (8),
    активный trial, истёкший платный и лендинговый ключ."""
    now = _now_ms()
    await register_user(backend, 5001)
    paid_7 = await insert_key(backend, 5001, tariff_id=STANDARD_TARIFF_ID, expiry_time=now + 10 * DAY_MS)
    paid_8 = await insert_key(backend, 5001, tariff_id=FAMILY_TARIFF_ID, expiry_time=now + 2 * DAY_MS)
    trial = await issue_trial(backend, 5002)
    expired = await insert_key(backend, 5003, tariff_id=STANDARD_TARIFF_ID, expiry_time=now - DAY_MS)
    landing = await insert_key(backend, -1, tariff_id=999, expiry_time=now + 10 * DAY_MS)
    await backend.reload_cache()
    return {"paid_7": paid_7, "paid_8": paid_8, "trial": trial["email"], "expired": expired, "landing": landing}


async def test_preview_counts_only_active_paid_keys(backend):
    keys = await _seed_mixed_keys(backend)

    data = await _preview(backend, days=3)

    assert data["total_keys"] == 2
    assert data["total_users"] == 1
    assert {t["tariff_id"]: t["keys"] for t in data["by_tariff"]} == {
        STANDARD_TARIFF_ID: 1,
        FAMILY_TARIFF_ID: 1,
    }
    old_min = await backend.pool.fetchval(
        "SELECT MIN(expiry_time) FROM keys WHERE email = ANY($1::text[])",
        [keys["paid_7"], keys["paid_8"]],
    )
    assert data["min_expiry_time"] == old_min
    assert data["new_expiry_min"] == old_min + 3 * DAY_MS


async def test_bulk_renew_extends_only_active_paid_keys(backend):
    keys = await _seed_mixed_keys(backend)
    paid_before = await db_key(backend, keys["paid_7"])
    trial_before = await db_key(backend, keys["trial"])
    expired_before = await db_key(backend, keys["expired"])
    landing_before = await db_key(backend, keys["landing"])
    await backend.pool.execute(
        "UPDATE keys SET notified_24h = TRUE, notified_10h = TRUE, used_traffic = 5.0 WHERE email = $1",
        keys["paid_7"],
    )
    await backend.reload_cache()

    resp = await _start(backend, days=3)
    assert resp.status_code == 202, resp.text
    started = resp.json()
    assert started["total_keys"] == 2

    done = await _finished(backend, started["job_id"])

    assert done["status"] == "done"
    assert done["processed"] == 2
    assert done["success"] == 2
    assert done["failed"] == 0

    renewed = await db_key(backend, keys["paid_7"])
    assert renewed["expiry_time"] == paid_before["expiry_time"] + 3 * DAY_MS
    assert renewed["notified_24h"] is False
    assert renewed["notified_10h"] is False
    assert renewed["used_traffic"] == 0.0
    panel = backend.fakes.panel_client(keys["paid_7"])
    assert panel["client"]["expiryTime"] == renewed["expiry_time"]

    for email, before in (
        (keys["trial"], trial_before),
        (keys["expired"], expired_before),
        (keys["landing"], landing_before),
    ):
        after = await db_key(backend, email)
        assert after["expiry_time"] == before["expiry_time"]


async def test_panel_failure_leaves_db_untouched(backend):
    now = _now_ms()
    await register_user(backend, 5101)
    email = await insert_key(backend, 5101, tariff_id=STANDARD_TARIFF_ID, expiry_time=now + 10 * DAY_MS)
    await backend.reload_cache()
    before = await db_key(backend, email)
    backend.fakes.fail_panel("clients/update")

    resp = await _start(backend, days=3)
    assert resp.status_code == 202, resp.text
    done = await _finished(backend, resp.json()["job_id"])

    assert done["status"] == "done"
    assert done["failed"] == 1
    assert done["success"] == 0
    after = await db_key(backend, email)
    assert after["expiry_time"] == before["expiry_time"]
    assert backend.fakes.panel_client(email)["client"]["expiryTime"] == before["expiry_time"]


async def test_start_refuses_mismatched_expected_total(backend):
    await _seed_mixed_keys(backend)

    resp = await _start(backend, days=3, expected_total=99)

    assert resp.status_code == 409
    assert "total_keys" in resp.json()["detail"]


async def test_start_refuses_when_no_active_paid_keys(backend):
    await register_user(backend, 5201)
    await insert_key(backend, 5201, tariff_id=STANDARD_TARIFF_ID, expiry_time=_now_ms() - DAY_MS)

    resp = await _start(backend, days=3)

    assert resp.status_code == 409


async def test_cancel_stops_remaining_keys(backend, monkeypatch):
    from app.factories import bulk_renewal_jobs
    from services.core.keys import bulk_renewal

    now = _now_ms()
    await register_user(backend, 5301)
    for _ in range(20):
        await insert_key(backend, 5301, tariff_id=STANDARD_TARIFF_ID, expiry_time=now + 10 * DAY_MS)
    await backend.reload_cache()

    async def slow_renew(pool, service_data, cache, xui, email, days):
        await asyncio.sleep(0.05)
        return 0

    monkeypatch.setattr(bulk_renewal, "renew_key_by_days", slow_renew)

    resp = await _start(backend, days=3)
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    while (await _status(backend, job_id))["processed"] == 0:
        await asyncio.sleep(0)

    cancel = await backend.client.post(
        f"/api/v1/admin/keys/bulk-renewals/{job_id}/cancel", headers=_admin_headers()
    )
    assert cancel.status_code == 200, cancel.text

    done = await _finished(backend, job_id)
    assert done["status"] == "cancelled"
    assert done["cancelled"] is True
    assert done["processed"] < done["total_keys"]
    assert await bulk_renewal_jobs().get(job_id) is not None


async def test_second_start_conflicts_with_running_job(backend, monkeypatch):
    from services.core.keys import bulk_renewal

    now = _now_ms()
    await register_user(backend, 5401)
    await insert_key(backend, 5401, tariff_id=STANDARD_TARIFF_ID, expiry_time=now + 10 * DAY_MS)
    await backend.reload_cache()

    gate = asyncio.Event()

    async def blocked_renew(pool, service_data, cache, xui, email, days):
        await gate.wait()
        return 0

    monkeypatch.setattr(bulk_renewal, "renew_key_by_days", blocked_renew)

    first = await _start(backend, days=3)
    assert first.status_code == 202, first.text
    second = await _start(backend, days=3)
    assert second.status_code == 409
    assert second.json()["detail"]["job_id"] == first.json()["job_id"]

    gate.set()
    await _finished(backend, first.json()["job_id"])


async def test_old_mass_renew_reports_panel_failure(backend):
    now = _now_ms()
    await register_user(backend, 5501)
    email = await insert_key(backend, 5501, tariff_id=STANDARD_TARIFF_ID, expiry_time=now + 10 * DAY_MS)
    await backend.reload_cache()
    before = await db_key(backend, email)
    backend.fakes.fail_panel("clients/update")

    resp = await backend.client.post(
        "/api/v1/admin/keys/renewals",
        json={"emails": [email], "days": 3},
        headers=_admin_headers(),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] == 0
    assert body["failed"] == 1
    assert body["results"][0]["success"] is False
    assert (await db_key(backend, email))["expiry_time"] == before["expiry_time"]
