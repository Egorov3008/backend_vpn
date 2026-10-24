-- Миграция (идемпотентная): промокоды со сроком действия и лимитом активаций.
--
-- promo_codes — сам код: скидка в процентах, окно действия, лимит активаций,
-- для кого (только новые / все), на какую операцию (покупка / продление / обе),
-- на сколько месяцев распространяется скидка, личный код (owner_tg_id).
--
-- promo_redemptions — одна строка на пару (код, пользователь): reserved, пока
-- счёт со скидкой не оплачен, used после оплаты. Резерв старше
-- PROMO_RESERVATION_TTL_MINUTES место в лимите не держит.
--
-- payments.promo_code_id / promo_discount — каким кодом и на сколько уменьшен счёт
-- (для статистики и для отметки used после оплаты).
--
-- Применять на БД вручную, до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261024_promo_codes.sql
-- Безопасна к повторному запуску. Создаёт таблицы и добавляет nullable/default-колонки.

BEGIN;

CREATE TABLE IF NOT EXISTS promo_codes
(
    id               SERIAL PRIMARY KEY,
    code             TEXT        NOT NULL,
    discount_percent INTEGER     NOT NULL CHECK (discount_percent BETWEEN 1 AND 100),
    starts_at        TIMESTAMPTZ NOT NULL,
    ends_at          TIMESTAMPTZ NOT NULL,
    max_redemptions  INTEGER CHECK (max_redemptions IS NULL OR max_redemptions > 0),
    audience         TEXT        NOT NULL DEFAULT 'new' CHECK (audience IN ('new', 'all')),
    operations       TEXT        NOT NULL DEFAULT 'any'
                     CHECK (operations IN ('create_key', 'renew_key', 'any')),
    max_months       INTEGER CHECK (max_months IS NULL OR max_months > 0),
    owner_tg_id      BIGINT,
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_by       BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ends_at > starts_at)
);

-- Код ищется без учёта регистра.
CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_codes_code ON promo_codes (upper(code));

CREATE TABLE IF NOT EXISTS promo_redemptions
(
    id              SERIAL PRIMARY KEY,
    promo_id        INTEGER     NOT NULL REFERENCES promo_codes (id) ON DELETE CASCADE,
    tg_id           BIGINT      NOT NULL,
    payment_id      TEXT,
    status          TEXT        NOT NULL DEFAULT 'reserved' CHECK (status IN ('reserved', 'used')),
    discount_amount REAL        NOT NULL DEFAULT 0,
    was_new_user    BOOLEAN     NOT NULL,
    reserved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used_at         TIMESTAMPTZ,
    UNIQUE (promo_id, tg_id)
);

CREATE INDEX IF NOT EXISTS idx_promo_redemptions_promo_status ON promo_redemptions (promo_id, status);

ALTER TABLE payments ADD COLUMN IF NOT EXISTS promo_code_id INTEGER;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS promo_discount REAL NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_payments_promo_code_id ON payments (promo_code_id) WHERE promo_code_id IS NOT NULL;

COMMIT;
