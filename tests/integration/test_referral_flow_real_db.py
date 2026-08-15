"""Integration test for the full referral bonus flow against a real DB.

This test exercises the production path:
1. Create a referrer and generate their referral link.
2. Create a referred user with referral_id set to the referrer's tg_id
   and a referral_redemption row.
3. Create a paid payment for the referred user.
4. Call PaymentRouter.route(payment_id) with the real db pool.
5. Assert that the referrer gets 10% balance bonus and the referred user
   gets check_referral=true and +3 days added to any keys.

Skipped unless TEST_DATABASE_URL is set. For local docker compose:
    TEST_DATABASE_URL=postgresql://egorov:tMtB1Ri9JRphMct@127.0.0.1:5433/bot_db \
        pytest tests/integration/test_referral_flow_real_db.py -v
"""
import os
import time
import uuid

import asyncpg
import pytest

from app.factories import build_payment_router
from config import settings
from database.service import DataService
from models import PaymentModel, User, Key, ReferralLink, ReferralRedemption
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

DDL_CLEANUP = """
DROP TABLE IF EXISTS referral_rewards CASCADE;
DROP TABLE IF EXISTS referral_redemptions CASCADE;
DROP TABLE IF EXISTS referral_links CASCADE;
DROP TABLE IF EXISTS referrals CASCADE;
DROP TABLE IF EXISTS keys CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS servers CASCADE;
"""

DDL_SETUP = """
CREATE TABLE IF NOT EXISTS users (
    tg_id bigint PRIMARY KEY,
    username text,
    first_name text,
    last_name text,
    language_code text,
    is_bot boolean DEFAULT false,
    created_at timestamptz DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz DEFAULT CURRENT_TIMESTAMP,
    is_admin boolean DEFAULT false,
    server_id integer,
    trial integer,
    referral_id integer,
    check_referral boolean,
    is_blocked boolean NOT NULL DEFAULT false,
    balance real NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS referral_links (
    id SERIAL PRIMARY KEY,
    referrer_tg_id BIGINT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referral_redemptions (
    id SERIAL PRIMARY KEY,
    referral_link_id INTEGER NOT NULL,
    referred_tg_id BIGINT NOT NULL UNIQUE,
    redeemed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referral_rewards (
    id SERIAL PRIMARY KEY,
    referrer_tg_id BIGINT NOT NULL,
    reward_type TEXT NOT NULL,
    reward_value DECIMAL(10,2) NOT NULL,
    awarded_at TIMESTAMPTZ DEFAULT NOW(),
    is_claimed BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    payment_id TEXT UNIQUE,
    tg_id BIGINT NOT NULL,
    amount REAL NOT NULL DEFAULT 0.0,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    payment_type TEXT NOT NULL,
    number_of_months INTEGER NOT NULL DEFAULT 1,
    discount_percent INTEGER NOT NULL DEFAULT 0,
    referral_discount REAL NOT NULL DEFAULT 0.0,
    balance_discount REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS keys (
    tg_id BIGINT NOT NULL,
    client_id TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    expiry_time BIGINT NOT NULL,
    key TEXT NOT NULL,
    notified_10h boolean DEFAULT false,
    notified_24h boolean DEFAULT false,
    total_gb real NOT NULL DEFAULT 10.0,
    reset_date bigint NOT NULL DEFAULT 0,
    used_traffic real NOT NULL DEFAULT 0.0,
    tariff_id integer,
    inbound_id integer,
    tariff_description text,
    name_tariff text,
    amount real,
    limit_ip integer,
    period integer,
    server_info jsonb,
    notified_expired_grace boolean DEFAULT false,
    landing_uid varchar(64),
    converted_tg_id bigint,
    CONSTRAINT uq_keys_email UNIQUE (email)
);
CREATE UNIQUE INDEX IF NOT EXISTS keys_pkey ON keys (tg_id, client_id);

CREATE TABLE IF NOT EXISTS servers (
    id SERIAL PRIMARY KEY,
    cluster_name TEXT NOT NULL,
    server_name TEXT NOT NULL,
    api_url TEXT NOT NULL,
    subscription_url TEXT NOT NULL,
    login TEXT NOT NULL,
    password TEXT NOT NULL
);
"""

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set — need a real Postgres (see docstring)",
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=2)
    async with p.acquire() as conn:
        await conn.execute(DDL_CLEANUP)
        await conn.execute(DDL_SETUP)
    yield p
    async with p.acquire() as conn:
        await conn.execute(DDL_CLEANUP)
    await p.close()


from services.cache.storage import CacheStorage


@pytest.fixture
async def service_data():
    storage = CacheStorage()
    cache = CacheService(storage=storage)
    data_service = DataService()
    return ServiceDataModel(cache_service=cache, data_service=data_service)


@pytest.fixture
async def payment_router(pool, service_data):
    cache = service_data.cache_service
    data_service = service_data.data_service
    return build_payment_router(pool, service_data, cache, data_service, notifier=None)


async def _insert_user(conn, user: User):
    await conn.execute(
        """
        INSERT INTO users (tg_id, username, first_name, balance, referral_id, check_referral)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (tg_id) DO UPDATE
        SET username=EXCLUDED.username,
            first_name=EXCLUDED.first_name,
            balance=EXCLUDED.balance,
            referral_id=EXCLUDED.referral_id,
            check_referral=EXCLUDED.check_referral
        """,
        user.tg_id,
        user.username,
        user.first_name,
        user.balance,
        getattr(user, "referral_id", None),
        getattr(user, "check_referral", False),
    )


async def _insert_referral_link(conn, link: ReferralLink):
    row = await conn.fetchrow(
        """
        INSERT INTO referral_links (referrer_tg_id, token)
        VALUES ($1, $2)
        RETURNING id
        """,
        link.referrer_tg_id,
        link.token,
    )
    return row["id"]


async def _insert_referral_redemption(conn, redemption: ReferralRedemption):
    await conn.execute(
        """
        INSERT INTO referral_redemptions (referral_link_id, referred_tg_id)
        VALUES ($1, $2)
        ON CONFLICT (referred_tg_id) DO NOTHING
        """,
        redemption.referral_link_id,
        redemption.referred_tg_id,
    )


async def _insert_payment(conn, payment: PaymentModel):
    await conn.execute(
        """
        INSERT INTO payments (payment_id, tg_id, amount, status, payment_type, number_of_months)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        payment.payment_id,
        payment.tg_id,
        payment.amount,
        payment.status,
        payment.payment_type,
        payment.number_of_months,
    )


async def _insert_key(conn, key: Key):
    await conn.execute(
        """
        INSERT INTO keys (tg_id, client_id, email, created_at, expiry_time, key, inbound_id)
        VALUES ($1::bigint, $2, $3, $4::bigint, $5::bigint, $6, $7)
        """,
        key.tg_id,
        key.client_id,
        key.email,
        key.created_at,
        key.expiry_time,
        key.key,
        key.inbound_id,
    )


@pytest.mark.asyncio
async def test_full_referral_bonus_flow(payment_router, pool, service_data):
    """End-to-end: first payment of a referred user triggers referrer bonus."""
    referrer = User(
        tg_id=1001,
        username="referrer",
        first_name="Referrer",
        balance=0.0,
    )
    referred = User(
        tg_id=2002,
        username="referred",
        first_name="Referred",
        balance=0.0,
        referral_id=1001,
        check_referral=False,
    )

    payment_amount = 500.0
    expected_bonus = round(payment_amount * 0.10, 2)

    async with pool.acquire() as conn:
        await _insert_user(conn, referrer)
        await _insert_user(conn, referred)

        link_id = await _insert_referral_link(
            conn,
            ReferralLink(referrer_tg_id=referrer.tg_id, token="ref_integrationtest"),
        )
        await _insert_referral_redemption(
            conn,
            ReferralRedemption(referral_link_id=link_id, referred_tg_id=referred.tg_id),
        )

        now_ms = int(time.time() * 1000)
        email = f"referred_{uuid.uuid4().hex[:8]}@test.com"
        await _insert_key(
            conn,
            Key(
                tg_id=referred.tg_id,
                client_id=str(uuid.uuid4()),
                email=email,
                created_at=now_ms,
                expiry_time=now_ms + 30 * 24 * 3600 * 1000,
                key="vless://test",
                inbound_id=11,
            ),
        )

        payment_id = f"pay_ref_{uuid.uuid4().hex[:12]}"
        await _insert_payment(
            conn,
            PaymentModel(
                payment_id=payment_id,
                tg_id=referred.tg_id,
                amount=payment_amount,
                status="pending",
                payment_type="create_key|1",
                number_of_months=1,
            ),
        )

    # Act: route the payment. Because there is no notifier, no Telegram message is sent.
    # The create_key step needs a server/tariff. We mock XUI and notifications by
    # monkeypatching the services used inside the router.
    from unittest.mock import AsyncMock, MagicMock, patch

    # Provide a fake tariff so that PaymentProcessor.extract_operation can resolve tariff_id=1
    fake_tariff = MagicMock()
    fake_tariff.amount = payment_amount
    fake_tariff.period = 30
    fake_tariff.name_tariff = "Test"
    fake_tariff.traffic_limit = 100
    fake_tariff.limit_ip = 1
    fake_tariff.id = 1

    async def fake_get_data(tariff_id, conn=None):
        return fake_tariff

    service_data.tariffs.get_data = fake_get_data

    # Stub out the XUI side of KeyCreationService so we don't need a real 3x-UI panel.
    router = payment_router
    original_creation_process = router.creation_service.process

    async def fake_creation_process(tariff_id):
        # Insert a synthetic key row directly so the router can "send" a notification.
        # Return the minimal shape that send_notification expects.
        now_ms = int(time.time() * 1000)
        fake_email = f"created_{uuid.uuid4().hex[:8]}@test.com"
        async with pool.acquire() as c:
            await _insert_key(
                c,
                Key(
                    tg_id=referred.tg_id,
                    client_id=str(uuid.uuid4()),
                    email=fake_email,
                    created_at=now_ms,
                    expiry_time=now_ms + 30 * 24 * 3600 * 1000,
                    key="vless://created",
                    inbound_id=11,
                ),
            )
        return {
            "tg_id": referred.tg_id,
            "email": fake_email,
            "expiry_time": now_ms + 30 * 24 * 3600 * 1000,
            "server_info": {"subscription_url": "https://test"},
        }

    router.creation_service.process = fake_creation_process
    router.creation_service.send_notification = AsyncMock()

    try:
        await router.route(payment_id)
    finally:
        router.creation_service.process = original_creation_process

    # Assert payment succeeded
    async with pool.acquire() as conn:
        payment_row = await conn.fetchrow(
            "SELECT status FROM payments WHERE payment_id=$1", payment_id
        )
        assert payment_row is not None
        assert payment_row["status"] == "succeeded"

        # Referrer balance increased by 10%
        referrer_row = await conn.fetchrow(
            "SELECT balance FROM users WHERE tg_id=$1", referrer.tg_id
        )
        assert referrer_row is not None
        assert round(float(referrer_row["balance"]), 2) == expected_bonus

        # Referred user marked as processed
        referred_row = await conn.fetchrow(
            "SELECT check_referral FROM users WHERE tg_id=$1", referred.tg_id
        )
        assert referred_row is not None
        assert referred_row["check_referral"] is True

        # Reward row created
        reward_row = await conn.fetchrow(
            "SELECT reward_value FROM referral_rewards WHERE referrer_tg_id=$1",
            referrer.tg_id,
        )
        assert reward_row is not None
        assert round(float(reward_row["reward_value"]), 2) == expected_bonus

        # Referred key was extended by +3 days (259_200_000 ms)
        key_row = await conn.fetchrow(
            "SELECT expiry_time FROM keys WHERE tg_id=$1 ORDER BY email", referred.tg_id
        )
        assert key_row is not None
        assert key_row["expiry_time"] >= now_ms + 30 * 24 * 3600 * 1000 + 3 * 24 * 3600 * 1000 - 1000


@pytest.mark.asyncio
async def test_no_bonus_without_referral(pool, service_data):
    """A regular user without referral_id gets no bonus."""
    from unittest.mock import AsyncMock, MagicMock

    regular = User(tg_id=3003, username="regular", first_name="Regular", balance=0.0)
    async with pool.acquire() as conn:
        await _insert_user(conn, regular)
        payment_id = f"pay_noref_{uuid.uuid4().hex[:12]}"
        await _insert_payment(
            conn,
            PaymentModel(
                payment_id=payment_id,
                tg_id=regular.tg_id,
                amount=500.0,
                status="pending",
                payment_type="create_key|1",
                number_of_months=1,
            ),
        )

    router = build_payment_router(pool, service_data, service_data.cache_service, service_data.data_service, notifier=None)

    fake_tariff = MagicMock()
    fake_tariff.amount = 500.0
    fake_tariff.period = 30
    fake_tariff.name_tariff = "Test"
    fake_tariff.traffic_limit = 100
    fake_tariff.limit_ip = 1
    fake_tariff.id = 1
    service_data.tariffs.get_data = lambda tariff_id, conn=None: fake_tariff

    router.creation_service.process = AsyncMock(return_value={"key": "k"})
    router.creation_service.send_notification = AsyncMock()

    await router.route(payment_id)

    async with pool.acquire() as conn:
        reward_count = await conn.fetchval(
            "SELECT count(*) FROM referral_rewards WHERE referrer_tg_id=$1", regular.tg_id
        )
        assert reward_count == 0
