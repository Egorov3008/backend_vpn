"""Общие шаги интеграционных тестов: подготовка данных через API и чтение строк из БД."""
import time
import uuid
from typing import Optional

from tests.integration.conftest import Backend


async def register_user(backend: Backend, tg_id: int, **fields) -> dict:
    resp = await backend.client.post("/api/v1/users/register", json={"tg_id": tg_id, **fields})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def issue_trial(backend: Backend, tg_id: int) -> dict:
    """Новый пользователь с триальным ключом (ключ есть и в БД, и в панели)."""
    await register_user(backend, tg_id)
    resp = await backend.client.post("/api/v1/keys/trial", params={"tg_id": tg_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def db_user(backend: Backend, tg_id: int) -> Optional[dict]:
    row = await backend.pool.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)
    return dict(row) if row else None


async def db_key(backend: Backend, email: str) -> Optional[dict]:
    row = await backend.pool.fetchrow("SELECT * FROM keys WHERE email = $1", email)
    return dict(row) if row else None


async def db_keys_of(backend: Backend, tg_id: int) -> list[dict]:
    rows = await backend.pool.fetch("SELECT * FROM keys WHERE tg_id = $1 ORDER BY created_at", tg_id)
    return [dict(r) for r in rows]


async def db_payment(backend: Backend, payment_id: str) -> Optional[dict]:
    row = await backend.pool.fetchrow("SELECT * FROM payments WHERE payment_id = $1", payment_id)
    return dict(row) if row else None


async def insert_key(
    backend: Backend,
    tg_id: int,
    *,
    tariff_id: int,
    expiry_time: int,
    email: Optional[str] = None,
    inbound_id: int = 11,
    in_panel: bool = True,
) -> str:
    """Ключ в БД (и клиент в панели), минуя API: для состояний, которые через API не получить.

    После вызова нужен ``backend.reload_cache()``, как после синхронизации."""
    email = email or f"it-{uuid.uuid4().hex[:10]}"
    client_id = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    await backend.pool.execute(
        "INSERT INTO keys (tg_id, client_id, email, created_at, expiry_time, key, inbound_id, tariff_id)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        tg_id, client_id, email, now_ms, expiry_time, f"{backend.fakes.url}/sub/{email}", inbound_id, tariff_id,
    )
    if in_panel:
        backend.fakes.add_panel_client(email, client_id, expiry_time, [inbound_id])
    return email
