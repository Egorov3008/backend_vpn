-- Миграция (идемпотентная): токены входа в веб-кабинет через бота.
--
-- Зачем: у части пользователей нет доступа к Telegram, пока у них нет VPN,
-- поэтому веб-кабинет не использует Telegram Login Widget. Сайт создаёт токен,
-- показывает ссылку t.me/<бот>?start=weblogin_<токен>, пользователь подтверждает
-- вход в боте, сайт получает сессию (api/v1/web.py).
--
-- Хранятся только SHA-256 хеши: token_hash — от токена из ссылки (его видит бот),
-- poll_hash — от секрета в HttpOnly cookie браузера, который начал вход. Сессию
-- забирает только этот браузер.
--
-- Применять на БД платформы (продакшн) вручную, до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261015_web_login_tokens.sql
-- Безопасна к повторному запуску. Только создаёт новую таблицу.

BEGIN;

CREATE TABLE IF NOT EXISTS web_login_tokens
(
    token_hash   TEXT PRIMARY KEY,
    poll_hash    TEXT NOT NULL UNIQUE,
    landing_uid  TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'confirmed', 'used')),
    tg_id        BIGINT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_web_login_tokens_expires_at
    ON web_login_tokens(expires_at);

COMMIT;
