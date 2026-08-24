#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# VPN Platform — импорт проекта на новый сервер
#
# Использование:
#   bash tools/db/migration_import.sh ARCHIVE_PATH NEW_DOMAIN [TARGET_DIR]
#
# Пример:
#   bash tools/db/migration_import.sh \
#     /opt/vpn-migration/vpn-platform-20260620-120000.tar.gz \
#     your-new-domain.com \
#     /home/admin/platform-backend
# ===================================================================

ARCHIVE_PATH="${1:?Укажите путь к архиву миграции (tar.gz)}"
NEW_DOMAIN="${2:?Укажите новый домен, например vpn.example.com}"
TARGET_DIR="${3:-/home/admin/platform-backend}"

WORK_DIR="$(mktemp -d)"
BACKUP_DIR="$(dirname "${ARCHIVE_PATH}")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[IMPORT]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }

# -------------------------------------------------------------------
# Проверки
# -------------------------------------------------------------------
if [[ ! -f "${ARCHIVE_PATH}" ]]; then
    err "Архив не найден: ${ARCHIVE_PATH}"
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    err "Docker не найден. Установите Docker и Docker Compose plugin."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    err "Docker Compose plugin не найден."
    exit 1
fi

log "Распаковываю архив..."
tar -xzf "${ARCHIVE_PATH}" -C "${WORK_DIR}"

# Находим единственную директорию vpn-platform-* внутри WORK_DIR
EXTRACTED_DIR=$(find "${WORK_DIR}" -maxdepth 1 -type d -name 'vpn-platform-*' | head -n 1)
if [[ -z "${EXTRACTED_DIR}" ]]; then
    err "Не удалось найти директорию vpn-platform-* после распаковки"
    exit 1
fi

if [[ ! -f "${EXTRACTED_DIR}/project/docker-compose.yml" ]]; then
    err "В архиве не найден docker-compose.yml"
    exit 1
fi

if [[ ! -f "${EXTRACTED_DIR}/db_backup/"*.custom.dump ]]; then
    err "В архиве не найден дамп БД (*.custom.dump)"
    exit 1
fi

# -------------------------------------------------------------------
# Перенос проекта
# -------------------------------------------------------------------
log "Копирую проект в ${TARGET_DIR}..."
mkdir -p "${TARGET_DIR}"
rsync -a --delete "${EXTRACTED_DIR}/project/" "${TARGET_DIR}/"

# -------------------------------------------------------------------
# Загружаем .env
# -------------------------------------------------------------------
if [[ ! -f "${TARGET_DIR}/.env" ]]; then
    err "В ${TARGET_DIR} отсутствует .env после распаковки"
    exit 1
fi

# Безопасно читаем DB-переменные из .env (не используем source, т.к. там JSON-массивы)
read_env_var() {
    local file="$1" key="$2"
    grep -E "^${key}=" "${file}" 2>/dev/null | head -n1 | cut -d'=' -f2- | sed -E 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^["\x27]|["\x27]$//g'
}

DB_NAME=$(read_env_var "${TARGET_DIR}/.env" DB_NAME)
DB_USER=$(read_env_var "${TARGET_DIR}/.env" DB_USER)
DB_PASSWORD=$(read_env_var "${TARGET_DIR}/.env" DB_PASSWORD)

if [[ -z "${DB_NAME}" || -z "${DB_USER}" || -z "${DB_PASSWORD}" ]]; then
    err "В .env не заданы DB_NAME / DB_USER / DB_PASSWORD"
    exit 1
fi

# -------------------------------------------------------------------
# Обновляем домен в конфигах
# -------------------------------------------------------------------
OLD_DOMAIN=""
if [[ -f "${TARGET_DIR}/nginx/nginx.conf" ]]; then
    OLD_DOMAIN=$(grep -oE 'server_name [^;]+;' "${TARGET_DIR}/nginx/nginx.conf" | head -n1 | awk '{print $2}' | tr -d ';' || true)
fi

if [[ -n "${OLD_DOMAIN}" && "${OLD_DOMAIN}" != "${NEW_DOMAIN}" ]]; then
    warn "Обнаружен старый домен в nginx.conf: ${OLD_DOMAIN} → ${NEW_DOMAIN}"
    sed -i "s/server_name ${OLD_DOMAIN}/server_name ${NEW_DOMAIN}/g" "${TARGET_DIR}/nginx/nginx.conf"
    log "Обновлён server_name в nginx/nginx.conf"
fi

# Обновляем WEBHOOK_BASE_URL и URL_BOT в .env
if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY' "${TARGET_DIR}/.env" "${NEW_DOMAIN}" "${BOT_NAME:-your_bot_username}"
import re, sys
path, domain, bot_name = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

# WEBHOOK_BASE_URL
if re.search(r'^WEBHOOK_BASE_URL=', text, flags=re.M):
    text = re.sub(
        r'^(WEBHOOK_BASE_URL=https?://)([^/\s]+)(.*)$',
        rf'\1{domain}\3',
        text,
        flags=re.M
    )
    # Если в значении нет протокола, просто подставляем https://domain
    text = re.sub(
        r'^(WEBHOOK_BASE_URL=)(?!https?://)(\S+)$',
        rf'\1https://{domain}',
        text,
        flags=re.M
    )
else:
    text += f'\nWEBHOOK_BASE_URL=https://{domain}\n'

# URL_BOT — если там tg.me/username, оставляем username; если просто домен — заменяем
# Заменяем только явные ссылки на старый домен (редко)
old_domain_match = re.search(r'^WEBHOOK_BASE_URL=https?://([^/\s]+)', text, flags=re.M)
old_domain = old_domain_match.group(1) if old_domain_match else None

if old_domain and old_domain != domain:
    text = re.sub(
        r'^(URL_BOT=)(https?://)?' + re.escape(old_domain) + r'(.*)$',
        rf'\1https://t.me/{bot_name}\3',
        text,
        flags=re.M
    )

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
print('ENV updated')
PY
else
    warn "python3 не найден — WEBHOOK_BASE_URL придётся обновить вручную в .env"
fi

# Перечитываем только DB-переменные после правки
DB_NAME=$(read_env_var "${TARGET_DIR}/.env" DB_NAME)
DB_USER=$(read_env_var "${TARGET_DIR}/.env" DB_USER)
DB_PASSWORD=$(read_env_var "${TARGET_DIR}/.env" DB_PASSWORD)

log "Текущие критичные настройки:"
info "  DOMAIN:        ${NEW_DOMAIN}"
info "  WEBHOOK_BASE_URL: $(read_env_var "${TARGET_DIR}/.env" WEBHOOK_BASE_URL)"
info "  DATABASE_URL:  $(read_env_var "${TARGET_DIR}/.env" DATABASE_URL)"

# -------------------------------------------------------------------
# SSL-сертификаты
# -------------------------------------------------------------------
CERT_DIR="${TARGET_DIR}/nginx_certs"
FULLCHAIN="${CERT_DIR}/fullchain.pem"
PRIVKEY="${CERT_DIR}/privkey.pem"

need_cert=false
if [[ ! -f "${FULLCHAIN}" || ! -f "${PRIVKEY}" ]]; then
    warn "SSL-сертификаты не найдены в ${CERT_DIR}"
    need_cert=true
else
    # Проверяем, что сертификат выдан для нового домена
    cert_domain=$(openssl x509 -in "${FULLCHAIN}" -noout -subject 2>/dev/null | grep -oE 'CN *= *[^/]+' | sed 's/CN *= *//' || true)
    if [[ -n "${cert_domain}" && "${cert_domain}" != "${NEW_DOMAIN}" && "${cert_domain}" != "*.${NEW_DOMAIN#*.}" ]]; then
        warn "Сертификат выдан для ${cert_domain}, а новый домен ${NEW_DOMAIN}"
        need_cert=true
    fi
fi

if [[ "${need_cert}" == true ]]; then
    warn "Требуется получить новый SSL-сертификат."
    read -r -p "Запустить Certbot standalone для ${NEW_DOMAIN}? [Y/n]: " answer <&1 || true
    answer=${answer:-Y}
    if [[ "${answer}" =~ ^[Yy]$ ]]; then
        if ! command -v certbot >/dev/null 2>&1; then
            log "Устанавливаю certbot..."
            apt-get update && apt-get install -y certbot || true
        fi
        mkdir -p "${CERT_DIR}"
        certbot certonly --standalone --non-interactive --agree-tos \
            -m "admin@${NEW_DOMAIN}" -d "${NEW_DOMAIN}" \
            --cert-path "${FULLCHAIN}" --key-path "${PRIVKEY}" \
            || true
        # Если certbot положил сертификаты в стандартное место — скопируем
        LETSENCRYPT_DIR="/etc/letsencrypt/live/${NEW_DOMAIN}"
        if [[ -f "${LETSENCRYPT_DIR}/fullchain.pem" && -f "${LETSENCRYPT_DIR}/privkey.pem" ]]; then
            cp "${LETSENCRYPT_DIR}/fullchain.pem" "${FULLCHAIN}"
            cp "${LETSENCRYPT_DIR}/privkey.pem" "${PRIVKEY}"
        fi
    fi
fi

if [[ ! -f "${FULLCHAIN}" || ! -f "${PRIVKEY}" ]]; then
    err "SSL-сертификаты всё ещё отсутствуют. Получите их вручную:"
    err "  certbot certonly --standalone -d ${NEW_DOMAIN}"
    err "Затем скопируйте fullchain.pem и privkey.pem в ${CERT_DIR}"
    exit 1
fi

log "SSL-сертификаты готовы."

# -------------------------------------------------------------------
# Запуск Postgres и восстановление БД
# -------------------------------------------------------------------
log "Запускаю PostgreSQL..."
(
    cd "${TARGET_DIR}"
    docker compose up -d postgres
)

POSTGRES_CONTAINER=""
for i in {1..60}; do
    POSTGRES_CONTAINER=$(docker compose -f "${TARGET_DIR}/docker-compose.yml" ps -q postgres 2>/dev/null || true)
    if [[ -n "${POSTGRES_CONTAINER}" ]] && docker exec "${POSTGRES_CONTAINER}" pg_isready -U "${DB_USER}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if [[ -z "${POSTGRES_CONTAINER}" ]]; then
    err "PostgreSQL не запустился за 60 секунд"
    exit 1
fi

# Определяем путь к дампу
DUMP_FILE=$(find "${EXTRACTED_DIR}/db_backup" -name '*.custom.dump' | head -n 1)
if [[ -z "${DUMP_FILE}" ]]; then
    err "Не найден дамп БД"
    exit 1
fi

log "Восстанавливаю базу ${DB_NAME} из дампа..."

# Создаём БД, если не существует
if ! docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    psql -U "${DB_USER}" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'" | grep -q 1; then
    docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
        psql -U "${DB_USER}" -d postgres -c "CREATE DATABASE \"${DB_NAME}\";"
fi

# Закрываем активные сессии
log "Закрываю активные сессии к ${DB_NAME}..."
docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    psql -U "${DB_USER}" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();" > /dev/null 2>&1 || true

# Восстановление
docker exec -i "${POSTGRES_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" > /dev/null 2>&1 || true

# Копируем дамп внутрь контейнера и запускаем pg_restore
DUMP_BASENAME=$(basename "${DUMP_FILE}")
docker cp "${DUMP_FILE}" "${POSTGRES_CONTAINER}:/tmp/${DUMP_BASENAME}"
docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    pg_restore -U "${DB_USER}" -d "${DB_NAME}" --no-owner --no-privileges --clean --if-exists \
    "/tmp/${DUMP_BASENAME}" || warn "pg_restore завершился с предупреждениями (обычно нормально)"

# Убираем все внешние ключи (single-panel / loose schema mode).
# Дамп со старого сервера несёт FK (в т.ч. fk_user_server), которые в рабочей
# схеме давно сняты. Жёсткий fk_user_server блокирует авто-регистрацию при пустой
# таблице servers (конфиг сервера берётся из .env через get_env_server).
log "Удаляю внешние ключи из восстановленной схемы..."
docker exec -i -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    psql -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 > /dev/null 2>&1 <<'SQL' || warn "не удалось удалить FK"
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT n.nspname AS s, c.relname AS t, con.conname AS cn
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE con.contype = 'f' AND n.nspname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I', r.s, r.t, r.cn);
  END LOOP;
END $$;
SQL

# Проверяем, что БД не пустая
TABLE_COUNT=$(docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    psql -U "${DB_USER}" -d "${DB_NAME}" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
log "Восстановлено таблиц: ${TABLE_COUNT}"

if [[ "${TABLE_COUNT}" -lt 5 ]]; then
    err "Восстановлено слишком мало таблиц. Проверьте дамп."
    exit 1
fi

# Применяем web/migrations поверх (idempotent)
log "Применяю web миграции (idempotent)..."
for f in $(ls "${TARGET_DIR}/web/migrations/"*.sql 2>/dev/null | sort); do
    case "$(basename "$f")" in
        *drop*) warn "  skip drop migration: $f" ;;
        *)
            log "  applying: $(basename "$f")"
            docker exec -i "${POSTGRES_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" \
                -v ON_ERROR_STOP=0 -f - < "$f" > /dev/null 2>&1 || warn "    warning"
            ;;
    esac
done

# -------------------------------------------------------------------
# Сборка и запуск всех сервисов
# -------------------------------------------------------------------
log "Собираю и запускаю сервисы..."
(
    cd "${TARGET_DIR}"
    docker compose up --build -d
)

# -------------------------------------------------------------------
# Ожидание и health check
# -------------------------------------------------------------------
log "Жду healthcheck backend и web..."
HEALTHY=false
for i in {1..60}; do
    if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1 || curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 2
done

if [[ "${HEALTHY}" != true ]]; then
    warn "Healthcheck backend/web не прошёл. Смотрите логи:"
    warn "  cd ${TARGET_DIR} && docker compose logs -f"
else
    log "Backend/web отвечают на /health"
fi

log "Статус сервисов:"
(
    cd "${TARGET_DIR}"
    docker compose ps
)

# -------------------------------------------------------------------
# Очистка
# -------------------------------------------------------------------
rm -rf "${WORK_DIR}"

# -------------------------------------------------------------------
# Итог
# -------------------------------------------------------------------
echo ""
log "Миграция завершена!"
echo ""
info "Проект развёрнут в: ${TARGET_DIR}"
info "Домен:              ${NEW_DOMAIN}"
info "WEBHOOK_BASE_URL:   ${WEBHOOK_BASE_URL:-https://${NEW_DOMAIN}}"
echo ""
warn "Обязательно выполните после миграции:"
echo "  1. Обновите webhook URL в YooKassa: https://${NEW_DOMAIN}/api/v1/payments/webhook"
echo "  2. Проверьте доступность https://${NEW_DOMAIN}/"
echo "  3. Проверьте работу бота в Telegram"
echo "  4. Проверьте подключение к 3x-UI: XUI_API_URL=${XUI_API_URL:-(не задан)}"
echo "  5. Рассмотрите смену BOT_SECRET_KEY / ADMIN_API_KEY / SECRET_KEY"
echo "  6. Удалите архив миграции с обоих серверов"
echo ""
