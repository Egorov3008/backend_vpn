"""Ключи (/api/v1/keys) на живой БД и заглушке 3x-UI: что видит пользователь и что лежит в панели."""
import time

import pytest

from tests.integration.conftest import PAID_INBOUNDS, TRIAL_TARIFF_ID
from tests.integration.helpers import db_key, db_keys_of, db_user, insert_key, issue_trial, register_user

DAY_MS = 24 * 3600 * 1000


async def test_trial_key_is_created_in_db_and_panel(backend):
    key = await issue_trial(backend, 1001)

    row = await db_key(backend, key["email"])
    assert row["tg_id"] == 1001
    assert row["tariff_id"] == TRIAL_TARIFF_ID
    # Пробный тариф — 7 дней
    assert abs(row["expiry_time"] - (time.time() * 1000 + 7 * DAY_MS)) < 60_000

    panel = backend.fakes.panel_client(key["email"])
    assert panel is not None
    assert panel["client"]["expiryTime"] == row["expiry_time"]
    assert sorted(panel["inboundIds"]) == PAID_INBOUNDS

    assert (await db_user(backend, 1001))["trial"] == 1


async def test_second_trial_is_refused(backend):
    await issue_trial(backend, 1002)

    resp = await backend.client.post("/api/v1/keys/trial", params={"tg_id": 1002})

    assert resp.status_code == 403
    assert len(await db_keys_of(backend, 1002)) == 1


async def test_legacy_user_with_null_trial_gets_trial(backend):
    await backend.pool.execute("INSERT INTO users (tg_id) VALUES (1003)")
    await backend.pool.execute("ALTER TABLE users ALTER COLUMN trial DROP NOT NULL")
    await backend.pool.execute("UPDATE users SET trial = NULL WHERE tg_id = 1003")
    await backend.reload_cache()

    resp = await backend.client.post("/api/v1/keys/trial", params={"tg_id": 1003})

    assert resp.status_code == 200, resp.text
    assert len(await db_keys_of(backend, 1003)) == 1


async def test_user_sees_only_own_keys(backend):
    mine = await issue_trial(backend, 1004)
    await issue_trial(backend, 1005)

    resp = await backend.client.get("/api/v1/keys/", params={"tg_id": 1004})

    assert resp.status_code == 200
    assert [k["email"] for k in resp.json()] == [mine["email"]]


async def test_user_deletes_own_key(backend):
    key = await issue_trial(backend, 1006)

    resp = await backend.client.delete(f"/api/v1/keys/{key['email']}", params={"tg_id": 1006})

    assert resp.status_code == 204
    assert await db_key(backend, key["email"]) is None
    assert backend.fakes.panel_client(key["email"]) is None
    listed = await backend.client.get("/api/v1/keys/", params={"tg_id": 1006})
    assert listed.json() == []


async def test_foreign_key_is_not_deleted(backend):
    key = await issue_trial(backend, 1007)
    await register_user(backend, 1008)

    resp = await backend.client.delete(f"/api/v1/keys/{key['email']}", params={"tg_id": 1008})

    assert resp.status_code == 403
    assert await db_key(backend, key["email"]) is not None
    assert backend.fakes.panel_client(key["email"]) is not None


async def test_free_renewal_extends_db_and_panel_equally(backend):
    key = await issue_trial(backend, 1009)
    before = (await db_key(backend, key["email"]))["expiry_time"]

    resp = await backend.client.patch(
        f"/api/v1/keys/{key['email']}",
        json={"tg_id": 1009, "tariff_id": TRIAL_TARIFF_ID, "number_of_months": 1},
    )

    assert resp.status_code == 200, resp.text
    after = (await db_key(backend, key["email"]))["expiry_time"]
    assert after > before
    assert backend.fakes.panel_client(key["email"])["client"]["expiryTime"] == after


async def test_renewal_panel_failure_keeps_expiry(backend):
    await register_user(backend, 1010)
    expiry = int(time.time() * 1000) + 3 * DAY_MS
    email = await insert_key(backend, 1010, tariff_id=TRIAL_TARIFF_ID, expiry_time=expiry)
    await backend.reload_cache()
    backend.fakes.fail_panel("clients/update")

    resp = await backend.client.patch(
        f"/api/v1/keys/{email}", json={"tg_id": 1010, "tariff_id": TRIAL_TARIFF_ID, "number_of_months": 1}
    )

    assert resp.status_code == 502
    assert (await db_key(backend, email))["expiry_time"] == expiry
    assert backend.fakes.panel_client(email)["client"]["expiryTime"] == expiry


async def test_paid_tariff_cannot_be_taken_for_free(backend):
    await register_user(backend, 1011)

    resp = await backend.client.post("/api/v1/keys/", json={"tg_id": 1011, "tariff_id": 7})

    assert resp.status_code == 402
    assert await db_keys_of(backend, 1011) == []


async def test_delete_panel_failure_keeps_key(backend):
    key = await issue_trial(backend, 1012)
    backend.fakes.fail_panel("clients/del")

    resp = await backend.client.delete(f"/api/v1/keys/{key['email']}", params={"tg_id": 1012})

    assert resp.status_code == 500
    assert await db_key(backend, key["email"]) is not None


async def test_key_detail_shows_trial_state(backend):
    key = await issue_trial(backend, 1013)

    resp = await backend.client.get(f"/api/v1/keys/{key['email']}")

    assert resp.status_code == 200
    detail = resp.json()
    assert detail["is_active"] is True
    assert detail["is_trial"] is True
    assert detail["days_left"] in (6, 7)


@pytest.mark.parametrize(
    "method, url, kwargs",
    [
        ("get", "/api/v1/keys/missing", {}),
        ("delete", "/api/v1/keys/missing", {"params": {"tg_id": 1014}}),
        ("patch", "/api/v1/keys/missing", {"json": {"tg_id": 1014, "tariff_id": TRIAL_TARIFF_ID, "number_of_months": 1}}),
        ("post", "/api/v1/keys/", {"json": {"tg_id": 1014, "tariff_id": 999}}),
        ("post", "/api/v1/keys/", {"json": {"tg_id": 99999, "tariff_id": TRIAL_TARIFF_ID}}),
        ("post", "/api/v1/keys/trial", {"params": {"tg_id": 99999}}),
    ],
)
async def test_missing_objects_give_404(backend, method, url, kwargs):
    await register_user(backend, 1014)

    resp = await backend.client.request(method.upper(), url, **kwargs)

    assert resp.status_code == 404
    assert backend.fakes.state()["clients"] == {}
