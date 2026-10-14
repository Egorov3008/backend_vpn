-- Миграция (идемпотентная): момент успешного завершения платежа.
--
-- Зачем:
--   * payments.created_at — момент создания инвойса, а не оплаты. Для ежедневной
--     сводки успешных платежей за сутки нужен момент перехода в 'succeeded'.
--   * Исторические строки остаются с NULL: отчёт их не учитывает.
--
-- Применять на БД платформы (продакшн) вручную, до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261013_payment_succeeded_at.sql
-- Безопасна к повторному запуску. Применять после 20261012_payment_processing_token.sql.

BEGIN;

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS succeeded_at TIMESTAMP WITH TIME ZONE;

COMMIT;
