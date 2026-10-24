-- Миграция (идемпотентная): акция возврата для тех, у кого платный ключ истёк.
--
-- Одна строка на отправленное предложение. cohort = offer — человек получил
-- личный промокод (promo_id); cohort = control — то же напоминание без скидки,
-- чтобы видеть, сколько людей вернулось бы и так. delivered = false — Telegram
-- не доставил сообщение (бот заблокирован), повторно такому не пишем до
-- WINBACK_REPEAT_DAYS.
--
-- Применять на БД вручную, до выката нового кода (после 20261024_promo_codes.sql):
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261025_winback_offers.sql
-- Безопасна к повторному запуску. Только создаёт новую таблицу.

BEGIN;

CREATE TABLE IF NOT EXISTS winback_offers
(
    id        SERIAL PRIMARY KEY,
    tg_id     BIGINT      NOT NULL,
    cohort    TEXT        NOT NULL CHECK (cohort IN ('offer', 'control')),
    promo_id  INTEGER REFERENCES promo_codes (id) ON DELETE SET NULL,
    key_email TEXT,
    delivered BOOLEAN     NOT NULL DEFAULT TRUE,
    sent_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_winback_offers_tg_sent ON winback_offers (tg_id, sent_at);

COMMIT;
