"""Админские эндпоинты утверждённых inbound'ов панели 3x-UI.

Проверяем результат: строки approved_inbounds, набор inbound'ов клиентов в
заглушке панели, статусы задач. Внешние сервисы — заглушки стенда.
"""
import asyncio

import pytest

from tests.integration.conftest import STANDARD_TARIFF_ID
from tests.integration.helpers import (
    db_approved_ids,
    insert_key,
    panel_inbound_ids,
    set_approved_inbounds,
)

ADMIN_TG_ID = 7101


def _admin_headers() -> dict:
    from config import settings

    return {"X-API-Key": settings.admin_api_key, "X-Admin-Tg-Id": str(ADMIN_TG_ID)}


async def _wait(backend, job_id: str) -> dict:
    from app.factories import inbound_sync_jobs

    await inbound_sync_jobs().wait(job_id)
    resp = await backend.client.get(
        f"/api/v1/admin/inbound-sync/{job_id}", headers=_admin_headers()
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _seed_keys(backend, count: int = 2) -> list[str]:
    """Несколько ключей БД, у каждого клиент панели с набором [11]."""
    emails = []
    for i in range(count):
        email = await insert_key(
            backend,
            6000 + i,
            tariff_id=STANDARD_TARIFF_ID,
            expiry_time=1_900_000_000_000,
            inbound_id=11,
            inbound_ids=[11],
        )
        emails.append(email)
    await backend.reload_cache()
    return emails


# ---------- чтение ----------


async def test_list_inbounds_marks_approved(backend):
    await set_approved_inbounds(backend, 11)

    resp = await backend.client.get("/api/v1/admin/inbounds")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    flags = {item["inbound_id"]: item["approved"] for item in data["inbounds"]}
    assert flags == {11: True, 12: False}
    assert data["approved_ids"] == [11]
    assert data["missing_approved_ids"] == []


async def test_list_inbounds_reports_approved_missing_on_panel(backend):
    await set_approved_inbounds(backend, 11, 77)

    resp = await backend.client.get("/api/v1/admin/inbounds")

    assert resp.status_code == 200, resp.text
    assert resp.json()["missing_approved_ids"] == [77]


async def test_list_approved_returns_order(backend):
    await set_approved_inbounds(backend, 12, 11)

    resp = await backend.client.get("/api/v1/admin/inbounds/approved")

    assert resp.status_code == 200, resp.text
    assert resp.json()["approved_inbound_ids"] == [12, 11]


async def test_list_inbounds_panel_failure_is_502(backend):
    backend.fakes.fail_panel("inbounds/list")

    resp = await backend.client.get("/api/v1/admin/inbounds")

    assert resp.status_code == 502, resp.text


# ---------- approve ----------


async def test_approve_persists_and_syncs_all_db_clients(backend):
    backend.fakes.set_panel_inbounds(11, 12, 13)
    emails = await _seed_keys(backend, 2)
    # Ещё один клиент панели, которого нет в БД, — его трогать нельзя.
    backend.fakes.add_panel_client("panel-only@x", "c-only", 1_900_000_000_000, [11])
    before = backend.fakes.panel_client(emails[0])["client"]

    resp = await backend.client.post(
        "/api/v1/admin/inbounds/13/approve", json={"sync": True}, headers=_admin_headers()
    )

    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert await db_approved_ids(backend) == [11, 12, 13]

    status = await _wait(backend, job_id)
    assert status["status"] == "done", status
    assert status["total_clients"] == 2
    assert status["success"] == 2 and status["failed"] == 0

    for email in emails:
        assert sorted(await panel_inbound_ids(backend, email)) == [11, 12, 13]
        after = backend.fakes.panel_client(email)["client"]
        # Меняется только inbound-набор: срок, enable и владелец на месте.
        assert after["expiryTime"] == before["expiryTime"]
        assert after.get("enable") is True
        assert after.get("tgId") == before.get("tgId")

    assert sorted(await panel_inbound_ids(backend, "panel-only@x")) == [11]


async def test_approve_without_sync_only_persists(backend):
    resp = await backend.client.post(
        "/api/v1/admin/inbounds/12/approve", json={"sync": False}, headers=_admin_headers()
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["job_id"] is None
    assert body["approved_inbound_ids"] == [11, 12]
    # 12 уже утверждён сидом из .env — повторное одобрение идемпотентно.
    assert body["created"] is False


async def test_approve_inbound_absent_on_panel_is_404(backend):
    resp = await backend.client.post(
        "/api/v1/admin/inbounds/99/approve", json={"sync": False}, headers=_admin_headers()
    )

    assert resp.status_code == 404, resp.text
    assert await db_approved_ids(backend) == [11, 12]


async def test_approve_while_job_running_returns_409_but_persists(backend, monkeypatch):
    from services.core.panel import sync_jobs

    backend.fakes.set_panel_inbounds(11, 12, 13)
    await _seed_keys(backend, 1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_sync(xui, email, target, *, verify=True):
        started.set()
        await release.wait()

    monkeypatch.setattr(sync_jobs, "sync_one_client", slow_sync)

    first = await backend.client.post(
        "/api/v1/admin/inbound-sync", json={}, headers=_admin_headers()
    )
    assert first.status_code == 202, first.text
    await asyncio.wait_for(started.wait(), timeout=5)

    resp = await backend.client.post(
        "/api/v1/admin/inbounds/13/approve", json={"sync": True}, headers=_admin_headers()
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["job_id"] == first.json()["job_id"]
    # Одобрение уже сохранено, несмотря на 409.
    assert 13 in await db_approved_ids(backend)

    release.set()
    assert (await _wait(backend, first.json()["job_id"]))["status"] == "done"


# ---------- sync ----------


async def test_sync_with_explicit_target_detaches_extra(backend):
    emails = []
    for i in range(2):
        emails.append(
            await insert_key(
                backend,
                6100 + i,
                tariff_id=STANDARD_TARIFF_ID,
                expiry_time=1_900_000_000_000,
                inbound_id=11,
                inbound_ids=[11, 12],
            )
        )
    await backend.reload_cache()

    preview = await backend.client.post(
        "/api/v1/admin/inbound-sync/preview",
        json={"inbound_ids": [11]},
        headers=_admin_headers(),
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["target_inbound_ids"] == [11]
    assert preview.json()["removed_inbound_ids"] == [12]
    assert preview.json()["total_clients"] == 2

    resp = await backend.client.post(
        "/api/v1/admin/inbound-sync", json={"inbound_ids": [11]}, headers=_admin_headers()
    )
    assert resp.status_code == 202, resp.text

    status = await _wait(backend, resp.json()["job_id"])
    assert status["status"] == "done", status
    for email in emails:
        assert await panel_inbound_ids(backend, email) == [11]


async def test_sync_target_not_approved_is_422(backend):
    resp = await backend.client.post(
        "/api/v1/admin/inbound-sync", json={"inbound_ids": [11, 99]}, headers=_admin_headers()
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["invalid_inbound_ids"] == [99]


async def test_sync_target_missing_on_panel_is_422(backend):
    backend.fakes.set_panel_inbounds(12)

    resp = await backend.client.post(
        "/api/v1/admin/inbound-sync/preview", json={"inbound_ids": [11]}, headers=_admin_headers()
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["invalid_inbound_ids"] == [11]


async def test_sync_panel_failure_is_502(backend):
    backend.fakes.fail_panel("inbounds/list")

    resp = await backend.client.post(
        "/api/v1/admin/inbound-sync", json={}, headers=_admin_headers()
    )

    assert resp.status_code == 502, resp.text


async def test_sync_expected_total_mismatch_is_409(backend):
    await _seed_keys(backend, 1)

    resp = await backend.client.post(
        "/api/v1/admin/inbound-sync", json={"expected_total": 5}, headers=_admin_headers()
    )

    assert resp.status_code == 409, resp.text


async def test_sync_missing_panel_client_counts_as_failed(backend):
    email = await insert_key(
        backend,
        6500,
        tariff_id=STANDARD_TARIFF_ID,
        expiry_time=1_900_000_000_000,
        in_panel=False,
    )
    await backend.reload_cache()

    resp = await backend.client.post(
        "/api/v1/admin/inbound-sync", json={}, headers=_admin_headers()
    )
    assert resp.status_code == 202, resp.text

    status = await _wait(backend, resp.json()["job_id"])
    assert status["status"] == "done"
    assert status["failed"] == 1
    assert status["errors"][0]["email"] == email


async def test_second_start_409_and_cancel(backend, monkeypatch):
    from services.core.panel import sync_jobs

    await _seed_keys(backend, 1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_sync(xui, email, target, *, verify=True):
        started.set()
        await release.wait()

    monkeypatch.setattr(sync_jobs, "sync_one_client", slow_sync)

    first = await backend.client.post(
        "/api/v1/admin/inbound-sync", json={}, headers=_admin_headers()
    )
    assert first.status_code == 202, first.text
    job_id = first.json()["job_id"]
    await asyncio.wait_for(started.wait(), timeout=5)

    again = await backend.client.post(
        "/api/v1/admin/inbound-sync", json={}, headers=_admin_headers()
    )
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["job_id"] == job_id

    cancel = await backend.client.post(
        f"/api/v1/admin/inbound-sync/{job_id}/cancel", headers=_admin_headers()
    )
    assert cancel.status_code == 200, cancel.text
    release.set()

    status = await _wait(backend, job_id)
    assert status["status"] == "cancelled", status
    assert status["cancelled"] is True

    finished_cancel = await backend.client.post(
        f"/api/v1/admin/inbound-sync/{job_id}/cancel", headers=_admin_headers()
    )
    assert finished_cancel.status_code == 409, finished_cancel.text


async def test_unknown_job_is_404(backend):
    resp = await backend.client.get(
        "/api/v1/admin/inbound-sync/nope", headers=_admin_headers()
    )

    assert resp.status_code == 404, resp.text


# ---------- revoke ----------


async def test_revoke_last_inbound_is_409(backend):
    await set_approved_inbounds(backend, 11)

    resp = await backend.client.delete(
        "/api/v1/admin/inbounds/11", headers=_admin_headers()
    )

    assert resp.status_code == 409, resp.text
    assert await db_approved_ids(backend) == [11]


async def test_revoke_unknown_inbound_is_404(backend):
    resp = await backend.client.delete(
        "/api/v1/admin/inbounds/13", headers=_admin_headers()
    )

    assert resp.status_code == 404, resp.text


async def test_revoke_with_sync_detaches_inbound(backend):
    email = await insert_key(
        backend,
        6200,
        tariff_id=STANDARD_TARIFF_ID,
        expiry_time=1_900_000_000_000,
        inbound_id=11,
        inbound_ids=[11, 12],
    )
    await backend.reload_cache()

    resp = await backend.client.delete(
        "/api/v1/admin/inbounds/12", headers=_admin_headers()
    )

    assert resp.status_code == 202, resp.text
    assert await db_approved_ids(backend) == [11]

    status = await _wait(backend, resp.json()["job_id"])
    assert status["status"] == "done", status
    assert await panel_inbound_ids(backend, email) == [11]


async def test_revoke_without_sync_is_204(backend):
    resp = await backend.client.delete(
        "/api/v1/admin/inbounds/12",
        params={"sync": False},
        headers=_admin_headers(),
    )

    assert resp.status_code == 204, resp.text
    assert await db_approved_ids(backend) == [11]


# ---------- доступ ----------


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/v1/admin/inbounds/12/approve", {"sync": False}),
    ("delete", "/api/v1/admin/inbounds/12", None),
    ("post", "/api/v1/admin/inbound-sync", {}),
])
async def test_bot_secret_alone_cannot_mutate(backend, method, path, body):
    call = getattr(backend.client, method)
    kwargs = {"json": body} if body is not None else {}

    resp = await call(path, **kwargs)  # только X-Bot-Secret из фикстуры

    assert resp.status_code in (401, 403), resp.text
