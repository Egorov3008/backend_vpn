-- Миграция (идемпотентная): админский список утверждённых inbound'ов панели.
--
-- AVAILABLE_CONNECTIONS из .env — только первоначальный seed, когда таблица пуста.
-- Дальше источник истины — эта таблица (+ кеш CacheService.approved_inbounds).
--
-- Применять на БД вручную до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261104_approved_inbounds.sql
--
-- Безопасна к повторному запуску: только создаёт новую таблицу и индекс.

BEGIN;

CREATE TABLE IF NOT EXISTS approved_inbounds
(
    inbound_id       INTEGER     PRIMARY KEY,
    sort_order       INTEGER     NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_tg_id BIGINT
);

CREATE INDEX IF NOT EXISTS idx_approved_inbounds_order
    ON approved_inbounds (sort_order, inbound_id);

COMMIT;
