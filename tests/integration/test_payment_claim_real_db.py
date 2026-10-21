"""Интеграционные тесты: атомарный захват платежей и уникальность keys.payment_id на живом Postgres.

Проверяют реальный SQL PaymentRepository (claim/release/complete/mark_canceled_if_pending)
на схеме из db/schema_fixed.sql и миграций. Моки здесь не используются: гонка между
захватами воспроизводится настоящими конкурентными запросами. База и запуск — см.
tests/integration/conftest.py.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from database.service import PAYMENT_CLAIM_STALE_MINUTES, PaymentRepository
from models import PaymentModel


@pytest.fixture
def repo():
    return PaymentRepository(table_name="payments", model=PaymentModel)


async def _insert_payment(pool, payment_id: str, status: str = "pending") -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO payments (payment_id, tg_id, payment_type, status) VALUES ($1, 1, 'create_key|1', $2)",
            payment_id,
            status,
        )


async def _row(pool, payment_id: str):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT status, processing_token, processing_started_at FROM payments WHERE payment_id = $1",
            payment_id,
        )


@pytest.mark.asyncio
async def test_claim_pending_takes_pending_once(pool, repo):
    await _insert_payment(pool, "pay_once")

    first = await repo.claim_pending(pool, "pay_once")
    second = await repo.claim_pending(pool, "pay_once")

    assert first is not None
    assert second is None
    row = await _row(pool, "pay_once")
    assert row["status"] == "processing"
    assert row["processing_token"] == first


@pytest.mark.asyncio
async def test_concurrent_claims_yield_exactly_one_owner(pool, repo):
    await _insert_payment(pool, "pay_race")

    tokens = await asyncio.gather(*(repo.claim_pending(pool, "pay_race") for _ in range(20)))

    winners = [t for t in tokens if t is not None]
    assert len(winners) == 1


@pytest.mark.asyncio
async def test_complete_and_release_require_matching_token(pool, repo):
    await _insert_payment(pool, "pay_tok")
    token = await repo.claim_pending(pool, "pay_tok")

    assert await repo.release_claim(pool, "pay_tok", "wrong-token") is False
    assert await repo.complete_claim(pool, "pay_tok", "wrong-token") is False
    assert (await _row(pool, "pay_tok"))["status"] == "processing"

    assert await repo.complete_claim(pool, "pay_tok", token) is True
    row = await _row(pool, "pay_tok")
    assert row["status"] == "succeeded"
    assert row["processing_token"] is None


@pytest.mark.asyncio
async def test_release_returns_payment_to_pending_for_retry(pool, repo):
    await _insert_payment(pool, "pay_rel")
    token = await repo.claim_pending(pool, "pay_rel")

    assert await repo.release_claim(pool, "pay_rel", token) is True
    row = await _row(pool, "pay_rel")
    assert row["status"] == "pending"
    assert row["processing_token"] is None
    assert await repo.claim_pending(pool, "pay_rel") is not None


@pytest.mark.asyncio
async def test_stale_claim_is_taken_over_and_old_owner_is_fenced(pool, repo):
    await _insert_payment(pool, "pay_stale")
    old_token = await repo.claim_pending(pool, "pay_stale")
    # Имитируем упавший процесс: захват старше порога
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE payments SET processing_started_at = $2 WHERE payment_id = $1",
            "pay_stale",
            datetime.now(timezone.utc) - timedelta(minutes=PAYMENT_CLAIM_STALE_MINUTES + 1),
        )

    new_token = await repo.claim_pending(pool, "pay_stale")

    assert new_token is not None and new_token != old_token
    # Прежний владелец уже не может ни завершить, ни сбросить захват
    assert await repo.complete_claim(pool, "pay_stale", old_token) is False
    assert await repo.release_claim(pool, "pay_stale", old_token) is False
    assert await repo.complete_claim(pool, "pay_stale", new_token) is True


@pytest.mark.asyncio
async def test_fresh_claim_is_not_taken_over(pool, repo):
    await _insert_payment(pool, "pay_fresh")
    assert await repo.claim_pending(pool, "pay_fresh") is not None
    assert await repo.claim_pending(pool, "pay_fresh") is None


@pytest.mark.asyncio
async def test_mark_canceled_only_affects_pending(pool, repo):
    await _insert_payment(pool, "pay_c_pending", "pending")
    await _insert_payment(pool, "pay_c_processing", "pending")
    await repo.claim_pending(pool, "pay_c_processing")
    await _insert_payment(pool, "pay_c_succeeded", "succeeded")

    assert await repo.mark_canceled_if_pending(pool, "pay_c_pending") is True
    assert await repo.mark_canceled_if_pending(pool, "pay_c_processing") is False
    assert await repo.mark_canceled_if_pending(pool, "pay_c_succeeded") is False

    assert (await _row(pool, "pay_c_pending"))["status"] == "canceled"
    assert (await _row(pool, "pay_c_processing"))["status"] == "processing"
    assert (await _row(pool, "pay_c_succeeded"))["status"] == "succeeded"


@pytest.mark.asyncio
async def test_renewal_expiry_is_fixed_on_first_write_and_reused_on_retry(pool, repo):
    await _insert_payment(pool, "pay_renew")

    first = await repo.reserve_renewal_expiry(pool, "pay_renew", 5_000)
    # Повтор позже вычисляет другой срок, но получает уже зафиксированный
    second = await repo.reserve_renewal_expiry(pool, "pay_renew", 9_000)

    assert first == 5_000
    assert second == 5_000
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT renewal_expiry_time FROM payments WHERE payment_id = $1", "pay_renew"
        )
    assert stored == 5_000


_KEY_SQL = (
    "INSERT INTO keys (email, client_id, payment_id, tg_id, created_at, expiry_time, key, inbound_id)"
    " SELECT v.email, v.client_id, v.payment_id, 1, 0, 0, '', 11 FROM (VALUES {rows}) AS v(email, client_id, payment_id)"
)


@pytest.mark.asyncio
async def test_keys_payment_id_is_unique_when_set(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            _KEY_SQL.format(rows="('a@x', 'c1', 'pay_k')")
        )
        # Ключи без платежа (NULL) не конфликтуют между собой
        await conn.execute(_KEY_SQL.format(rows="('n1@x', 'c2', NULL), ('n2@x', 'c3', NULL)"))
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(_KEY_SQL.format(rows="('b@x', 'c4', 'pay_k')"))


@pytest.mark.asyncio
async def test_list_open_for_user_returns_only_fresh_open_payments_of_same_operation(pool, repo):
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    rows = [
        ("pay_open", 7, "create_key|1", "pending", None),
        ("pay_proc", 7, "create_key|1", "processing", None),
        ("pay_done", 7, "create_key|1", "succeeded", None),
        ("pay_canc", 7, "create_key|1", "canceled", None),
        ("pay_other_tariff", 7, "create_key|2", "pending", None),
        ("pay_other_user", 8, "create_key|1", "pending", None),
        ("pay_stale", 7, "create_key|1", "pending", old),
    ]
    async with pool.acquire() as conn:
        for payment_id, tg_id, payment_type, status, created_at in rows:
            await conn.execute(
                "INSERT INTO payments (payment_id, tg_id, payment_type, status, created_at) "
                "VALUES ($1, $2, $3, $4, COALESCE($5, NOW()))",
                payment_id, tg_id, payment_type, status, created_at,
            )

    found = await repo.list_open_for_user(pool, 7, "create_key|1", 60)

    assert {p.payment_id for p in found} == {"pay_open", "pay_proc"}
