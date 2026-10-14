-- Миграция (идемпотентная): токен владельца захвата платежа (claim-token).
--
-- Зачем: захват может быть перехвачен по таймауту (PAYMENT_CLAIM_STALE_MINUTES).
-- Медленный прежний владелец не должен сбрасывать или завершать захват нового:
-- release/complete работают только при совпадении processing_token.
--
-- Применять на БД платформы (продакшн) вручную, до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261012_payment_processing_token.sql
-- Безопасна к повторному запуску. Применять после 20261011_payment_claim_and_key_payment_id.sql.

BEGIN;

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS processing_token TEXT;

COMMIT;
