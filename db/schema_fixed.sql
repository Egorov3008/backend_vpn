-- Core tables without dependencies
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

-- Tables that others depend on
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

-- Users table (depends on servers)
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
	CONSTRAINT users_pkey PRIMARY KEY (tg_id),
	CONSTRAINT fk_user_server FOREIGN KEY (server_id) REFERENCES public.servers(id) ON DELETE SET NULL
);

-- Inbound table (depends on servers)
CREATE TABLE IF NOT EXISTS inbound (
    id SERIAL PRIMARY KEY,
    server_id INTEGER NOT NULL,
    inbound_id INTEGER NOT NULL,
    name_inbound TEXT,
    UNIQUE (server_id, inbound_id),
    CONSTRAINT fk_inbound_server FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE RESTRICT
);

-- Tables that depend on users
CREATE TABLE IF NOT EXISTS payments
(
    id SERIAL PRIMARY KEY,
    payment_id TEXT UNIQUE,
    tg_id BIGINT NOT NULL,
    amount REAL NOT NULL DEFAULT 0.0,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    payment_type TEXT NOT NULL,
    number_of_months INTEGER NOT NULL DEFAULT 1,
    discount_percent INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (tg_id) REFERENCES users (tg_id)
);

-- Keys table (depends on users, inbound, tariff)
CREATE TABLE IF NOT EXISTS keys (
    tg_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    client_id TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    expiry_time BIGINT NOT NULL,
    key TEXT NOT NULL,
    total_gb REAL NOT NULL DEFAULT 0.0,
    reset_date BIGINT NOT NULL DEFAULT 0,
    inbound_id INTEGER NOT NULL REFERENCES inbound(id) ON DELETE CASCADE,
    notified_10h BOOLEAN NOT NULL DEFAULT FALSE,
    notified_24h BOOLEAN NOT NULL DEFAULT FALSE,
    tariff_id INTEGER REFERENCES tariff(id) ON DELETE SET NULL,
    tariff_description TEXT,
    name_tariff TEXT,
    amount REAL,
    limit_ip INTEGER,
    period INTEGER,
    used_traffic REAL NOT NULL DEFAULT 0.0,
    server_info JSONB,
    UNIQUE (tg_id, client_id)
);

-- Referral tables (depend on users)
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

CREATE TABLE IF NOT EXISTS referral_links (
    id SERIAL PRIMARY KEY,
    referrer_tg_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referral_redemptions (
    id SERIAL PRIMARY KEY,
    referral_link_id INTEGER NOT NULL REFERENCES referral_links(id) ON DELETE CASCADE,
    referred_tg_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    redeemed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referral_rewards (
    id SERIAL PRIMARY KEY,
    referrer_tg_id BIGINT NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    reward_type TEXT NOT NULL,
    reward_value TEXT NOT NULL,
    awarded_at TIMESTAMPTZ DEFAULT NOW(),
    is_claimed BOOLEAN DEFAULT FALSE
);

-- Notifications table
CREATE TABLE IF NOT EXISTS notifications
(
    tg_id BIGINT NOT NULL,
    notification_type TEXT NOT NULL,
    last_notification_time TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tg_id, notification_type)
);

-- Gift links table (depends on users)
CREATE TABLE IF NOT EXISTS gift_links
(
    id SERIAL PRIMARY KEY NOT NULL,
    sender_tg_id INTEGER NOT NULL,
    tariff_id INTEGER NOT NULL,
    token TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    recipient_tg_id BIGINT,
    email TEXT,
    used_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT fk_sender FOREIGN KEY (sender_tg_id) REFERENCES users (tg_id),
    CONSTRAINT fk_recipient FOREIGN KEY (recipient_tg_id) REFERENCES users (tg_id)
);

-- Stocks table
CREATE TABLE IF NOT EXISTS stocks
(
    id INTEGER PRIMARY KEY NOT NULL,
    discount_type TEXT NOT NULL,
    discount_percent REAL NOT NULL DEFAULT 0.0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expiry_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    limit_users INTEGER DEFAULT 0,
    count_users INTEGER DEFAULT 0
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_referrals_token ON referrals(token);
CREATE INDEX IF NOT EXISTS idx_referrals_active ON referrals(is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_referral_links_token ON referral_links(token);
CREATE INDEX IF NOT EXISTS idx_referral_links_referrer ON referral_links(referrer_tg_id);
CREATE INDEX IF NOT EXISTS idx_referral_redemptions_link ON referral_redemptions(referral_link_id);
CREATE INDEX IF NOT EXISTS idx_referral_rewards_referrer ON referral_rewards(referrer_tg_id);
