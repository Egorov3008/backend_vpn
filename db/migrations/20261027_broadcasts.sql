-- Миграция (идемпотентная): массовые рассылки из веб-админки.
--
-- broadcasts — одна строка на рассылку: что отправить (текст, медиа по file_id
-- пользовательского бота, кнопка), кому (audience) и статус задачи.
-- broadcast_recipients — снимок получателей на момент запуска, одна строка на
-- пользователя. Рассылка идёт по строкам pending; после рестарта backend
-- продолжает с того же места, уже отправленным повторно не пишет.
--
-- Применять на БД вручную, до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261027_broadcasts.sql
-- Безопасна к повторному запуску. Только создаёт новые таблицы.

BEGIN;

CREATE TABLE IF NOT EXISTS broadcasts
(
    id                   SERIAL PRIMARY KEY,
    admin_tg_id          BIGINT,
    audience             TEXT        NOT NULL,
    text                 TEXT        NOT NULL DEFAULT '',
    media_type           TEXT CHECK (media_type IN ('photo', 'video', 'animation', 'audio', 'document')),
    media_file_id        TEXT,
    media_file_name      TEXT,
    button_kind          TEXT CHECK (button_kind IN ('url', 'miniapp')),
    button_text          TEXT,
    button_url           TEXT,
    disable_notification BOOLEAN     NOT NULL DEFAULT FALSE,
    pin                  BOOLEAN     NOT NULL DEFAULT FALSE,
    status               TEXT        NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'cancel_requested', 'canceled', 'done', 'failed')),
    error                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts (status);

CREATE TABLE IF NOT EXISTS broadcast_recipients
(
    broadcast_id INTEGER     NOT NULL REFERENCES broadcasts (id) ON DELETE CASCADE,
    tg_id        BIGINT      NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'blocked', 'failed')),
    error        TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (broadcast_id, tg_id)
);

CREATE INDEX IF NOT EXISTS idx_broadcast_recipients_status ON broadcast_recipients (broadcast_id, status);

COMMIT;
