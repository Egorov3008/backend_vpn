-- Миграция (идемпотентная): атомарный захват платежей и уникальность ключа на платёж.
--
-- Зачем:
--   * payments.processing_started_at + статус 'processing' — захват платежа перед
--     созданием ключа (гонка вебхук YooKassa vs. поллинг мини-аппа давала два ключа).
--   * keys.payment_id с уникальным индексом — даже при ошибке в коде второй ключ
--     по тому же платежу не запишется в БД.
--
-- Применять на БД платформы (продакшн) вручную, до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261011_payment_claim_and_key_payment_id.sql
-- Безопасна к повторному запуску. Существующие ключи получают payment_id = NULL,
-- поэтому уникальный индекс создаётся без конфликтов.

BEGIN;

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE keys
    ADD COLUMN IF NOT EXISTS payment_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_keys_payment_id ON keys(payment_id)
    WHERE payment_id IS NOT NULL;

COMMIT;
