-- Миграция (идемпотентная): окно «только Telegram» после окончания подписки.
--
-- Зачем: с 3x-ui v3.8.0 (PR MHSanaei/3x-ui#6333) панель перестала отдавать внешние
-- ссылки (externalLinks, EXTERNAL_SUB_URL) у просроченного клиента. Бэкенд на
-- TELEGRAM_GRACE_DAYS дней сдвигает expiryTime клиента в панели и отвязывает его от
-- инбаундов, чтобы в подписке осталась только внешняя ссылка. Колонка хранит конец
-- окна; keys.expiry_time остаётся сроком оплаты.
--
-- Применять на БД платформы (продакшн) вручную, до выката нового кода:
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261018_keys_telegram_grace.sql
-- Безопасна к повторному запуску. Только добавляет nullable-колонку, данные не меняет.

BEGIN;

ALTER TABLE keys
    ADD COLUMN IF NOT EXISTS telegram_grace_until BIGINT;

COMMIT;
