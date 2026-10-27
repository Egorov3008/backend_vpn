-- Миграция (идемпотентная): несколько файлов (альбом) в одной рассылке.
--
-- media_items — JSON-список [{type, file_id, file_name}] всех файлов рассылки.
-- Старые колонки media_type/media_file_id/media_file_name хранят первый файл:
-- рассылки, созданные до миграции, читаются по ним.
--
-- Применять на БД вручную, до выката нового кода (после 20261027_broadcasts.sql):
--   docker exec -i <postgres-container> psql -U <DB_USER> -d <DB_NAME> -v ON_ERROR_STOP=1 \
--     < db/migrations/20261028_broadcast_media_items.sql
-- Безопасна к повторному запуску. Только добавляет колонку.

BEGIN;

ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS media_items JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMIT;
