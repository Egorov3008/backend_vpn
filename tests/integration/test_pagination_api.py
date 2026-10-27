"""Постраничные списки: страницы без пропусков и повторов, X-Total-Count — размер всего списка."""
import time

import pytest

from tests.integration.conftest import STANDARD_TARIFF_ID
from tests.integration.helpers import insert_key

OWNER = 4001
TOTAL = 5

LISTS = [
    ("/api/v1/keys/", {"tg_id": OWNER}, lambda body: [k["email"] for k in body]),
    ("/api/v1/payments/", {"tg_id": OWNER}, lambda body: [p["payment_id"] for p in body]),
    ("/api/v1/admin/users", {}, lambda body: [u["tg_id"] for u in body]),
    ("/api/v1/admin/keys", {"tg_id": OWNER}, lambda body: [k["email"] for k in body["keys"]]),
    ("/api/v1/admin/payments", {"tg_id": OWNER}, lambda body: [p["payment_id"] for p in body["payments"]]),
]


@pytest.fixture
async def filled(backend):
    expiry = int(time.time() * 1000) + 10 * 24 * 3600 * 1000
    for tg_id in range(OWNER, OWNER + TOTAL):
        await backend.pool.execute("INSERT INTO users (tg_id) VALUES ($1)", tg_id)
    for i in range(TOTAL):
        await insert_key(backend, OWNER, tariff_id=STANDARD_TARIFF_ID, expiry_time=expiry + i, in_panel=False)
        await backend.pool.execute(
            "INSERT INTO payments (payment_id, tg_id, amount, status, payment_type) VALUES ($1, $2, 199, 'succeeded', 'create_key|7')",
            f"page-{i}", OWNER,
        )
    # Чужие данные не должны попадать в списки пользователя
    await insert_key(backend, OWNER + 1, tariff_id=STANDARD_TARIFF_ID, expiry_time=expiry, in_panel=False)
    await backend.pool.execute(
        "INSERT INTO payments (payment_id, tg_id, amount, status, payment_type) VALUES ('foreign', $1, 199, 'pending', 'create_key|7')",
        OWNER + 1,
    )
    await backend.reload_cache()
    return backend


@pytest.mark.parametrize("url, params, ids", LISTS, ids=[u for u, _, _ in LISTS])
async def test_pages_cover_full_list_once(filled, url, params, ids):
    full = await filled.client.get(url, params=params)
    assert full.status_code == 200, full.text
    everything = ids(full.json())
    assert len(everything) == TOTAL

    pages = []
    for offset in range(0, TOTAL, 2):
        resp = await filled.client.get(url, params={**params, "limit": 2, "offset": offset})
        assert resp.headers["X-Total-Count"] == str(TOTAL)
        pages.extend(ids(resp.json()))

    assert sorted(map(str, pages)) == sorted(map(str, everything))


async def test_admin_finds_payment_by_part_of_id(filled):
    resp = await filled.client.get("/api/v1/admin/payments", params={"q": "FOREI"})

    assert resp.status_code == 200, resp.text
    assert [p["payment_id"] for p in resp.json()["payments"]] == ["foreign"]
    assert resp.headers["X-Total-Count"] == "1"
