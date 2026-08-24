#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# VPN Platform — экспорт ТОЛЬКО данных для миграции (код из GitHub)
#
# Использование:
#   bash tools/db/migration_export_data_only.sh [PROJECT_DIR] [BACKUP_DIR]
#
# По умолчанию:
#   PROJECT_DIR = текущая директория
#   BACKUP_DIR = /tmp/vpn-migration-data
#
# Создаёт:
#   - tar.gz c .env, сертификатами, дампом БД, логами и видео бота
#   - БЕЗ исходного кода (его заберём на новом сервере через git clone)
# ===================================================================

PROJECT_DIR="${1:-$(pwd)}"
BACKUP_DIR="${2:-/tmp/vpn-migration-data}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
ARCHIVE_NAME="vpn-migration-data-${TIMESTAMP}.tar.gz"
WORK_DIR="${BACKUP_DIR}/vpn-migration-data-${TIMESTAMP}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[EXPORT-DATA]${NC} $*"; }
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

mkdir -p "${WORK_DIR}/db_backup" "${WORK_DIR}/meta"

log "Начинаю hot-export данных из ${PROJECT_DIR}"
log "Рабочая директория: ${WORK_DIR}"
log "Работающие сервисы останавливаться НЕ будут"

# -------------------------------------------------------------------
# 1. Бэкап PostgreSQL (hot backup, без остановки сервисов)
# -------------------------------------------------------------------
log "Делаю hot backup базы ${DB_NAME}..."

POSTGRES_CONTAINER=$(docker compose -f "${PROJECT_DIR}/docker-compose.yml" ps -q postgres 2>/dev/null || true)
if [[ -z "${POSTGRES_CONTAINER:-}" ]]; then
    err "PostgreSQL-контейнер не запущен. Запустите сервисы: cd ${PROJECT_DIR} && docker compose up -d postgres"
    exit 1
fi

for i in {1..30}; do
    if docker exec "${POSTGRES_CONTAINER}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# --jobs=4 ускоряет дамп за счёт параллелизма, --compress=9 экономит место
docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fc -Z 9 \
    > "${WORK_DIR}/db_backup/${DB_NAME}.custom.dump"

docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fp \
    > "${WORK_DIR}/db_backup/${DB_NAME}.plain.sql"

log "Размер дампа custom: $(du -h "${WORK_DIR}/db_backup/${DB_NAME}.custom.dump" | cut -f1)"
log "Размер дампа plain:  $(du -h "${WORK_DIR}/db_backup/${DB_NAME}.plain.sql" | cut -f1)"

# -------------------------------------------------------------------
# 3. Копируем .env, сертификаты, логи и видео
# -------------------------------------------------------------------
cp "${PROJECT_DIR}/.env" "${WORK_DIR}/.env"

for subdir in nginx_certs bot/logs bot/logs_error bot/video_instructions; do
    src="${PROJECT_DIR}/${subdir}"
    if [[ -d "${src}" ]]; then
        mkdir -p "${WORK_DIR}/${subdir%/*}"
        cp -a "${src}" "${WORK_DIR}/${subdir}" || warn "Не удалось скопировать ${src}"
    fi
done

# -------------------------------------------------------------------
# 4. Метаданные
# -------------------------------------------------------------------
hostname > "${WORK_DIR}/meta/source_host.txt"
date -Iseconds > "${WORK_DIR}/meta/export_timestamp.txt"

# -------------------------------------------------------------------
# 5. Упаковка
# -------------------------------------------------------------------
log "Упаковываю архив..."
(
    cd "${BACKUP_DIR}"
    tar -czf "${ARCHIVE_NAME}" "vpn-migration-data-${TIMESTAMP}"
)

ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"
ARCHIVE_SIZE=$(du -h "${ARCHIVE_PATH}" | cut -f1)

log "Готово!"
log "Архив: ${ARCHIVE_PATH}"
log "Размер: ${ARCHIVE_SIZE}"

tar -tzf "${ARCHIVE_PATH}" > /dev/null

log "Восстановить на новом сервере можно командой:"
echo ""
echo "  git clone --branch main https://github.com/OWNER/REPO.git /home/admin/platform-backend"
echo "  bash /home/admin/platform-backend/tools/db/migration_import_from_git.sh \\"
echo "    \"https://github.com/OWNER/REPO.git\" \\"
echo "    \"main\" \\"
echo "    \"${ARCHIVE_PATH}\" \\"
echo "    \"your-new-domain.com\" \\"
echo "    \"/home/admin/platform-backend\""
echo ""

warn "Не забудьте после миграции обновить webhook URL в YooKassa!"
