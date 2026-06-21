-- Idempotent schema initialization for VPN platform (bot_db)
-- Compatible with PostgreSQL 16
-- Safe to re-run manually: all DDL uses IF NOT EXISTS or DO $$ guards.

-- ============================================================================
-- 1. Core tables without dependencies
-- ============================================================================

CREATE TABLE IF NOT EXISTS registrate_msg_user (
    tg_id int8 NOT NULL,
    is_msg int8 DEFAULT 0 NULL
);

CREATE TABLE IF NOT EXISTS cache (
    key TEXT NOT NULL PRIMARY KEY,
    value JSONB NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS mass_mailing (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    emoji TEXT NOT NULL
);

-- ============================================================================
-- 2. Servers & Tariffs (tables that others depend on)
-- ============================================================================

CREATE TABLE IF NOT EXISTS servers
(
    id               SERIAL PRIMARY KEY,
    cluster_name     TEXT NOT NULL,
    server_name      TEXT NOT NULL,
    api_url          TEXT NOT NULL,
    subscription_url TEXT NOT NULL,
    login            TEXT NOT NULL,
    password         TEXT NOT NULL,
    UNIQUE (cluster_name, server_name)
);

CREATE TABLE IF NOT EXISTS tariff
(
    id SERIAL PRIMARY KEY,
    name_tariff TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0.0,
    description TEXT,
    limit_ip INTEGER NOT NULL DEFAULT 0,
    period INTEGER NOT NULL DEFAULT 30,
    traffic_limit REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS inbound
(
    id SERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL,
    inbound_id INTEGER NOT NULL,
    name_inbound TEXT,
    UNIQUE (server_id, inbound_id)
);

-- ============================================================================
-- 3. Users (depends on servers)
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    tg_id int8 NOT NULL,
    username text NULL,
    first_name text NULL,
    last_name text NULL,
    language_code text NULL,
    balance REAL NOT NULL DEFAULT 0.0,
    is_bot bool DEFAULT false NULL,
    created_at timestamptz DEFAULT CURRENT_TIMESTAMP NULL,
    updated_at timestamptz DEFAULT CURRENT_TIMESTAMP NULL,
    is_admin bool DEFAULT false NULL,
    trial int4 DEFAULT 0 NOT NULL,
    server_id int4 NULL,
    check_referral bool DEFAULT false NULL,
    is_blocked bool DEFAULT false NULL,
    CONSTRAINT users_pkey PRIMARY KEY (tg_id)
    -- NOTE: FK fk_user_server (users.server_id -> servers.id) намеренно убран.
    -- Режим single-panel: таблица servers может быть пустой, конфиг сервера
    -- берётся из .env через get_env_server(). Жёсткий FK блокировал авто-регистрацию
    -- новых пользователей (bot шлёт server_id=2) при пустой servers.
    -- См. models/servers/server.py:get_env_server.
);

-- Add missing referral_id (migration 008)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'referral_id'
    ) THEN
        ALTER TABLE users ADD COLUMN referral_id INTEGER;
    END IF;
END $$;

-- ============================================================================
-- 4. Payments (depends on users)
-- ============================================================================

CREATE TABLE IF NOT EXISTS payments
(
    id             SERIAL PRIMARY KEY,
    payment_id     TEXT UNIQUE,
    tg_id          BIGINT NOT NULL,
    amount         REAL   NOT NULL DEFAULT 0.0,
    status         TEXT                     DEFAULT 'pending',
    created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    payment_type   TEXT NOT NULL,
    number_of_months INTEGER NOT NULL DEFAULT 1,
    discount_percent INTEGER NOT NULL DEFAULT 0,
    referral_discount REAL NOT NULL DEFAULT 0.0
);

-- Backfill safety: ensure existing rows have referral_discount before any NOT NULL enforcement
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'referral_discount'
    ) THEN
        UPDATE payments SET referral_discount = 0.0 WHERE referral_discount IS NULL;
        ALTER TABLE payments ALTER COLUMN referral_discount SET NOT NULL;
    ELSE
        ALTER TABLE payments ADD COLUMN referral_discount REAL NOT NULL DEFAULT 0.0;
    END IF;
END $$;

-- ============================================================================
-- 5. Keys (depends on users, inbound, tariff)
-- ============================================================================

CREATE TABLE IF NOT EXISTS keys
(
    tg_id BIGINT NOT NULL,
    client_id TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    expiry_time BIGINT NOT NULL,
    key TEXT NOT NULL,
    total_gb REAL NOT NULL DEFAULT 10.0,
    reset_date BIGINT NOT NULL DEFAULT 0,
    inbound_id INTEGER NOT NULL,
    notified_10h BOOLEAN NOT NULL DEFAULT FALSE,
    notified_24h BOOLEAN NOT NULL DEFAULT FALSE,
    tariff_id INTEGER,
    tariff_description TEXT,
    name_tariff TEXT,
    amount REAL,
    limit_ip INTEGER,
    period INTEGER,
    used_traffic REAL NOT NULL DEFAULT 0.0,
    server_info JSONB,
    UNIQUE (tg_id, client_id),
    UNIQUE (email)
);

-- Ensure total_gb default matches model default (migration 008)
DO $$
BEGIN
    ALTER TABLE keys ALTER COLUMN total_gb SET DEFAULT 10.0;
EXCEPTION
    WHEN undefined_column THEN
        RAISE NOTICE 'Column keys.total_gb does not exist yet';
END $$;

-- Ensure unique constraint on email exists (migration 008)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_keys_email' AND conrelid = 'keys'::regclass
    ) THEN
        ALTER TABLE keys ADD CONSTRAINT uq_keys_email UNIQUE (email);
    END IF;
END $$;

-- ============================================================================
-- 6. Referral tables (depend on users)
-- ============================================================================

CREATE TABLE IF NOT EXISTS referrals (
    referral_id SERIAL PRIMARY KEY,
    referrer_id BIGINT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    discount_percent REAL NOT NULL DEFAULT 15.0,
    max_usages INTEGER,
    current_usages INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS referral_links
(
    id SERIAL PRIMARY KEY,
    referrer_tg_id BIGINT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referral_redemptions
(
    id SERIAL PRIMARY KEY,
    referral_link_id INTEGER NOT NULL,
    referred_tg_id BIGINT NOT NULL,
    redeemed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referral_rewards
(
    id SERIAL PRIMARY KEY,
    referrer_tg_id BIGINT NOT NULL,
    reward_type TEXT NOT NULL,
    reward_value TEXT NOT NULL,
    awarded_at TIMESTAMPTZ DEFAULT NOW(),
    is_claimed BOOLEAN DEFAULT FALSE
);

-- ============================================================================
-- 7. Notifications
-- ============================================================================

CREATE TABLE IF NOT EXISTS notifications
(
    tg_id BIGINT NOT NULL,
    notification_type TEXT NOT NULL,
    last_notification_time TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tg_id, notification_type)
);

-- ============================================================================
-- 8. Gift links (depends on users)
-- ============================================================================

CREATE TABLE IF NOT EXISTS gift_links
(
    id              SERIAL PRIMARY KEY NOT NULL,
    sender_tg_id    INTEGER NOT NULL,
    tariff_id       INTEGER NOT NULL,
    token           TEXT NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    recipient_tg_id BIGINT,
    email           TEXT,
    used_at         TIMESTAMP WITH TIME ZONE
);

-- ============================================================================
-- 9. Stocks (per-user discount format; migration 008)
-- ============================================================================

-- Per-user stocks table (new format).
-- If an old global-promo stocks table exists, leave it for migrations to handle.
CREATE TABLE IF NOT EXISTS stocks
(
    tg_id       BIGINT PRIMARY KEY,
    stock_type  TEXT NOT NULL CHECK (stock_type IN ('fix', 'percent')),
    value       DECIMAL(10,2) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    valid_until TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================================
-- 10. Indexes
-- ============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'stocks' AND column_name = 'valid_until'
    ) THEN
        CREATE INDEX IF NOT EXISTS idx_stocks_active ON stocks(is_active, valid_until);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_referrals_token ON referrals(token);
CREATE INDEX IF NOT EXISTS idx_referrals_active ON referrals(is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_referral_links_token ON referral_links(token);
CREATE INDEX IF NOT EXISTS idx_referral_links_referrer ON referral_links(referrer_tg_id);
CREATE INDEX IF NOT EXISTS idx_referral_redemptions_link ON referral_redemptions(referral_link_id);
CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer ON referral_rewards(referrer_tg_id);

-- ============================================================================
-- 11. Self-healing: убрать ВСЕ внешние ключи (single-panel / loose schema mode)
-- Текущая рабочая БД не содержит FK; init-скрипт тоже их не объявляет выше.
-- Этот блок гарантированно удаляет любые FK, оставшиеся от старой «строгой»
-- схемы (например, при повторном ручном запуске поверх БД со старыми FK).
-- ============================================================================
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT n.nspname AS schema_name, c.relname AS table_name, con.conname AS constraint_name
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f'
          AND n.nspname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I',
                       r.schema_name, r.table_name, r.constraint_name);
    END LOOP;
END $$;
