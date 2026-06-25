"""Интеграционный тест: BaseRepository.update для keys против живой БД.

Воспроизводит production-схему keys (keys_pkey = UNIQUE(tg_id, client_id),
uq_keys_email = UNIQUE(email)) и проверяет, что перенос владельца ключа
(UPDATE по email со сменой tg_id) не падает в UniqueViolationError на
uq_keys_email — то есть тот самый 500 в /landing/claim/{uid}.

Пропускается, если не задана TEST_DATABASE_URL — чтобы не ломать обычный
`pytest` без БД:
    TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test pytest tests/integration
"""
import os
import time

import asyncpg
import pytest

from database.base import BaseRepository
from models.keys.key import Key

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

# Минимальная схема keys, воспроизводящая prod-индексы, на которых ломался баг.
DDL = """
CREATE TABLE IF NOT EXISTS keys (
    tg_id                  bigint NOT NULL,
    client_id              text   NOT NULL,
    email                  text   NOT NULL,
    created_at             bigint NOT NULL,
    expiry_time            bigint NOT NULL,
    key                    text   NOT NULL,
    notified_10h           boolean NOT NULL DEFAULT false,
    notified_24h           boolean NOT NULL DEFAULT false,
    total_gb               real    NOT NULL DEFAULT 10.0,
    reset_date             bigint  NOT NULL DEFAULT 0,
    used_traffic           real    NOT NULL DEFAULT 0.0,
    tariff_id              integer,
    inbound_id             integer,
    tariff_description     text,
    name_tariff            text,
    amount                 real,
    limit_ip               integer,
    period                 integer,
    server_info            jsonb,
    notified_expired_grace boolean NOT NULL DEFAULT false,
    landing_uid            varchar(64),
    converted_tg_id        bigint
);
CREATE UNIQUE INDEX IF NOT EXISTS keys_pkey   ON keys (tg_id, client_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_keys_email ON keys (email);
"""

DROP = "DROP TABLE IF EXISTS keys CASCADE;"


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL не задана — нужен живой Postgres (см. докстринг)",
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    async with p.acquire() as c:
        await c.execute(DROP)
        await c.execute(DDL)
    yield p
    async with p.acquire() as c:
        await c.execute(DROP)
    await p.close()


def _seed_landing_key() -> Key:
    """Лендинг-ключ: pseudo_tg_id (<0), ещё не привязан к реальному юзеру."""
    now_ms = int(time.time() * 1000)
    return Key(
        tg_id=-100542224,                 # pseudo
        client_id="4b4a9fb0-b792-4341-bc4f-7bab4f1d5b5b",
        email="ov8egq",
        expiry_time=now_ms + 24 * 3600 * 1000,   # 24ч
        key="vless://seed",
        inbound_id=2,
        landing_uid="cc7bf4e803eb4337",
    )


async def _insert(pool, key: Key):
    async with pool.acquire() as c:
        await c.execute(
            """INSERT INTO keys
               (tg_id, client_id, email, created_at, expiry_time, key,
                inbound_id, landing_uid)
               VALUES ($1::bigint, $2, $3, $4::bigint, $5::bigint, $6, $7, $8)""",
            key.tg_id, key.client_id, key.email, key.created_at,
            key.expiry_time, key.key, key.inbound_id, key.landing_uid,
        )


async def _fetch(pool, email: str):
    async with pool.acquire() as c:
        return await c.fetchrow(
            "SELECT tg_id, client_id, email, converted_tg_id, "
            "       tariff_id, expiry_time FROM keys WHERE email=$1",
            email,
        )


@pytest.mark.asyncio
async def test_email_update_transfers_owner_without_unique_violation(pool):
    """Регресс бага: перенос tg_id через update по email.

    Старый код делал INSERT ... ON CONFLICT (tg_id, client_id) — после смены
    tg_id конфликт не находил строку и INSERTил новую с тем же email →
    UniqueViolationError на uq_keys_email. Здесь не должно ни упасть, ни
    создать вторую строку.
    """
    repo = BaseRepository(table_name="keys", model=Key)

    landing = _seed_landing_key()
    await _insert(pool, landing)

    # Эмулируем claim_key: тот же ключ, но tg_id переносится на реального юзера.
    claimed = _seed_landing_key()
    claimed.tg_id = 7563318767
    claimed.converted_tg_id = 7563318767
    claimed.tariff_id = 10
    claimed.name_tariff = "Пробный"
    claimed.period = 7
    claimed.amount = 0
    claimed.limit_ip = 1

    # Главный assertion: не бросает UniqueViolationError.
    await repo.update(pool, {"email": claimed.email}, **claimed.to_dict())

    row = await _fetch(pool, "ov8egq")
    assert row is not None
    assert row["tg_id"] == 7563318767, "владелец не перенесён"
    assert row["converted_tg_id"] == 7563318767
    assert row["tariff_id"] == 10

    async with pool.acquire() as c:
        count = await c.fetchval("SELECT count(*) FROM keys WHERE email='ov8egq'")
    assert count == 1, f"ожидаема 1 строка, получилось {count} — UPSERT вставил дубликат"


@pytest.mark.asyncio
async def test_tg_id_update_keeps_upsert_inplace(pool):
    """Контроль: апдейт по tg_id остаётся UPSERT-ом (не должен ломать гонки)."""
    repo = BaseRepository(table_name="keys", model=Key)

    key = _seed_landing_key()
    await _insert(pool, key)

    key.expiry_time = key.expiry_time + 7 * 86400 * 1000
    await repo.update(pool, {"tg_id": key.tg_id}, **key.to_dict())

    row = await _fetch(pool, "ov8egq")
    assert row["tg_id"] == -100542224, "tg_id не должен измениться при апдейте по нему же"
    assert row["expiry_time"] == key.expiry_time

    async with pool.acquire() as c:
        count = await c.fetchval("SELECT count(*) FROM keys WHERE email='ov8egq'")
    assert count == 1, "UPSERT вставил дубликат вместо in-place обновления"