#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# VPN Platform — hot backup .env + PostgreSQL
#
# Использование:
#   bash tools/db/backup_db_and_env.sh [PROJECT_DIR] [BACKUP_DIR]
#
# По умолчанию:
#   PROJECT_DIR = /home/admin/platform-backend
#   BACKUP_DIR  = /tmp/vpn-backup
#
# Создаёт:
#   /tmp/vpn-backup/vpn-db-env-YYYYMMDD-HHMMSS.tar.gz
#
# Сервисы останавливаться НЕ будут — pg_dump через docker exec.
# ===================================================================

PROJECT_DIR="${1:-/home/admin/platform-backend}"
BACKUP_DIR="${2:-/tmp/vpn-backup}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
ARCHIVE_NAME="vpn-db-env-${TIMESTAMP}.tar.gz"
WORK_DIR="${BACKUP_DIR}/vpn-db-env-${TIMESTAMP}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[BACKUP]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# -------------------------------------------------------------------
# Проверки
# -------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    err "Docker не найден."
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
    err "В ${PROJECT_DIR} не найден docker-compose.yml. Укажите корень проекта."
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    err "В ${PROJECT_DIR} не найден .env."
    exit 1
fi

# Безопасно читаем DB-переменные из .env (не используем source, т.к. там JSON-массивы)
read_env_var() {
    local file="$1" key="$2"
    grep -E "^${key}=" "${file}" 2>/dev/null | head -n1 | cut -d'=' -f2- | sed -E 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^["\x27]|["\x27]$//g'
}

DB_NAME=$(read_env_var "${PROJECT_DIR}/.env" DB_NAME)
DB_USER=$(read_env_var "${PROJECT_DIR}/.env" DB_USER)
DB_PASSWORD=$(read_env_var "${PROJECT_DIR}/.env" DB_PASSWORD)

if [[ -z "${DB_NAME}" || -z "${DB_USER}" || -z "${DB_PASSWORD}" ]]; then
    err "В .env не заданы DB_NAME / DB_USER / DB_PASSWORD."
    exit 1
fi

mkdir -p "${WORK_DIR}/db_backup" "${WORK_DIR}/env"

log "Начинаю hot backup .env + БД из ${PROJECT_DIR}"
log "Сервисы останавливаться НЕ будут"

# -------------------------------------------------------------------
# 1. Бэкап PostgreSQL
# -------------------------------------------------------------------
POSTGRES_CONTAINER=$(docker compose -f "${PROJECT_DIR}/docker-compose.yml" ps -q postgres 2>/dev/null || true)
if [[ -z "${POSTGRES_CONTAINER:-}" ]]; then
    err "PostgreSQL-контейнер не запущен. Запустите сервисы: cd ${PROJECT_DIR} && docker compose up -d postgres"
    exit 1
fi

log "PostgreSQL контейнер: ${POSTGRES_CONTAINER}"

for i in {1..30}; do
    if docker exec "${POSTGRES_CONTAINER}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

log "Делаю hot backup базы ${DB_NAME}..."

docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fc -Z 9 \
    > "${WORK_DIR}/db_backup/${DB_NAME}.custom.dump"

docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fp \
    > "${WORK_DIR}/db_backup/${DB_NAME}.plain.sql"

log "Размер дампа custom: $(du -h "${WORK_DIR}/db_backup/${DB_NAME}.custom.dump" | cut -f1)"
log "Размер дампа plain:  $(du -h "${WORK_DIR}/db_backup/${DB_NAME}.plain.sql" | cut -f1)"

# -------------------------------------------------------------------
# 2. Копируем .env
# -------------------------------------------------------------------
cp "${PROJECT_DIR}/.env" "${WORK_DIR}/env/.env"

# -------------------------------------------------------------------
# 3. Метаданные
# -------------------------------------------------------------------
hostname > "${WORK_DIR}/meta_source_host.txt"
date -Iseconds > "${WORK_DIR}/meta_export_timestamp.txt"

# -------------------------------------------------------------------
# 4. Упаковка
# -------------------------------------------------------------------
log "Упаковываю архив..."
(
    cd "${BACKUP_DIR}"
    tar -czf "${ARCHIVE_NAME}" "vpn-db-env-${TIMESTAMP}"
)

ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"
ARCHIVE_SIZE=$(du -h "${ARCHIVE_PATH}" | cut -f1)

log "Готово!"
log "Архив: ${ARCHIVE_PATH}"
log "Размер: ${ARCHIVE_SIZE}"

# -------------------------------------------------------------------
# 5. Пост-проверка
# -------------------------------------------------------------------
tar -tzf "${ARCHIVE_PATH}" > /dev/null
log "Целостность архива проверена."

echo ""
echo "Перенесите архив на новый сервер, например:"
echo "  scp ${ARCHIVE_PATH} root@NEW_SERVER:/opt/"
echo ""
echo "И запустите там:"
echo "  bash tools/db/restore_db_and_env.sh ${ARCHIVE_PATH} [PROJECT_DIR]"
echo ""

warn "Не забудьте после восстановления обновить webhook URL в YooKassa!"
