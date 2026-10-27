-- Миграция (идемпотентная): журнал действий пользователей для вкладки «Поведение» в веб-админке.
--
-- user_events — одна строка на событие: кто (tg_id или анонимный id гостя сайта),
-- что (name из каталога services/user_events/catalog.py), откуда (source) и
-- короткие параметры (props, только поля, разрешённые каталогом). IP, user-agent,
-- ключи подключения и введённый текст сюда не пишутся.
-- Старые строки удаляет планировщик (EVENTS_RETENTION_DAYS).
--
-- Применять на БД вручную, до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261029_user_events.sql
-- Безопасна к повторному запуску. Только создаёт новую таблицу.

BEGIN;

CREATE TABLE IF NOT EXISTS user_events
(
    id         BIGSERIAL PRIMARY KEY,
    tg_id      BIGINT,
    anon_id    VARCHAR(64),
    name       TEXT        NOT NULL,
    source     TEXT        NOT NULL,
    props      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_events_name_created ON user_events (name, created_at);
CREATE INDEX IF NOT EXISTS idx_user_events_tg_created ON user_events (tg_id, created_at) WHERE tg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_user_events_created ON user_events (created_at);

COMMIT;
