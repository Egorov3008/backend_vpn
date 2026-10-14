"""PgAdvisoryPaymentClaim на настоящем Postgres: второй захват того же платежа
не проходит, пока первый держится, и проходит после освобождения.

Пропускается без TEST_DATABASE_URL.
"""
import os

import asyncpg
import pytest

from services.core.payment.claim import PgAdvisoryPaymentClaim

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def pool():
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=2, max_size=4)
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS payments (payment_id TEXT UNIQUE, status TEXT DEFAULT 'pending')"
        )
        await conn.execute("DELETE FROM payments WHERE payment_id LIKE 'claim_test_%'")
        await conn.execute("INSERT INTO payments (payment_id, status) VALUES ('claim_test_1', 'succeeded')")
    yield pool
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM payments WHERE payment_id LIKE 'claim_test_%'")
    await pool.close()


@pytest.mark.asyncio
async def test_second_claim_is_refused_while_first_is_held(pool):
    claim = PgAdvisoryPaymentClaim(pool)

    async with claim.hold("claim_test_1") as first:
        assert first.acquired
        assert first.db_status == "succeeded"
        async with claim.hold("claim_test_1") as second:
            assert not second.acquired
        async with claim.hold("claim_test_other") as other:
            assert other.acquired
            assert other.db_status is None

    async with claim.hold("claim_test_1") as again:
        assert again.acquired
