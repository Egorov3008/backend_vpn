#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# VPN Platform — экспорт проекта для миграции на новый сервер
#
# Использование:
#   bash tools/db/migration_export.sh [PROJECT_DIR] [BACKUP_DIR]
#
# По умолчанию:
#   PROJECT_DIR = текущая директория
#   BACKUP_DIR = /tmp/vpn-migration
#
# Создаёт:
#   - tar.gz с исходным кодом, .env, сертификатами, логами
#   - pg_dump базы в форматах custom и plain SQL
# ===================================================================

PROJECT_DIR="${1:-$(pwd)}"
BACKUP_DIR="${2:-/tmp/vpn-migration}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
ARCHIVE_NAME="vpn-platform-${TIMESTAMP}.tar.gz"
WORK_DIR="${BACKUP_DIR}/vpn-platform-${TIMESTAMP}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[EXPORT]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# -------------------------------------------------------------------
# Проверки
# -------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    err "Docker не найден. Установите Docker и Docker Compose plugin."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    err "Docker Compose plugin не найден."
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
    err "В ${PROJECT_DIR} не найден docker-compose.yml. Укажите корень проекта."
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
    err "В ${PROJECT_DIR} не найден .env. Без него миграция невозможна."
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

mkdir -p "${WORK_DIR}/project" "${WORK_DIR}/db_backup" "${WORK_DIR}/meta"

log "Начинаю hot-export проекта из ${PROJECT_DIR}"
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

# Custom format — компактный, быстрый restore через pg_restore; -Z 9 для сжатия
docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fc -Z 9 \
    > "${WORK_DIR}/db_backup/${DB_NAME}.custom.dump"

# Plain SQL — страховочный человекочитаемый дамп
docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fp \
    > "${WORK_DIR}/db_backup/${DB_NAME}.plain.sql"

# Размеры дампов
du -h "${WORK_DIR}/db_backup/${DB_NAME}.custom.dump"
du -h "${WORK_DIR}/db_backup/${DB_NAME}.plain.sql"

# -------------------------------------------------------------------
# 3. Копируем проект
# -------------------------------------------------------------------
log "Копирую файлы проекта..."

# rsync предпочтительнее, но если нет — cp
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude='.git' \
        --exclude='node_modules' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.pytest_cache' \
        --exclude='postgres_data' \
        --exclude='postgres_dev_data' \
        --exclude='*.tar.gz' \
        --exclude='bot/logs' \
        --exclude='bot/logs_error' \
        --exclude='bot/video_instructions' \
        "${PROJECT_DIR}/" "${WORK_DIR}/project/"
else
    # cp без exclude — потом удалим мусор
    cp -a "${PROJECT_DIR}/." "${WORK_DIR}/project/"
    find "${WORK_DIR}/project" -type d \( \
        -name '.git' -o -name 'node_modules' -o -name '__pycache__' -o -name '.pytest_cache' \
    \) -prune -exec rm -rf {} + 2>/dev/null || true
    find "${WORK_DIR}/project" -type f -name '*.pyc' -delete 2>/dev/null || true
fi

# -------------------------------------------------------------------
# 4. Явно копируем чувствительные и большие директории
# -------------------------------------------------------------------
for subdir in bot/logs bot/logs_error bot/video_instructions nginx_certs; do
    src="${PROJECT_DIR}/${subdir}"
    dst="${WORK_DIR}/project/${subdir}"
    if [[ -d "${src}" ]]; then
        mkdir -p "${dst%/*}"
        cp -a "${src}" "${dst}" || warn "Не удалось скопировать ${src}"
    fi
done

# -------------------------------------------------------------------
# 5. Метаданные
# -------------------------------------------------------------------
hostname > "${WORK_DIR}/meta/source_host.txt"
date -Iseconds > "${WORK_DIR}/meta/export_timestamp.txt"
docker compose -f "${PROJECT_DIR}/docker-compose.yml" images 2>/dev/null > "${WORK_DIR}/meta/docker_images.txt" || true

# -------------------------------------------------------------------
# 6. Упаковка
# -------------------------------------------------------------------
log "Упаковываю архив..."
(
    cd "${BACKUP_DIR}"
    tar -czf "${ARCHIVE_NAME}" "vpn-platform-${TIMESTAMP}"
)

ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"
ARCHIVE_SIZE=$(du -h "${ARCHIVE_PATH}" | cut -f1)

log "Готово!"
log "Архив: ${ARCHIVE_PATH}"
log "Размер: ${ARCHIVE_SIZE}"

# -------------------------------------------------------------------
# 7. Пост-проверки
# -------------------------------------------------------------------
log "Проверяю целостность архива..."
tar -tzf "${ARCHIVE_PATH}" > /dev/null

log "Восстановить на новом сервере можно командой:"
echo ""
echo "  bash tools/db/migration_import.sh \\"
echo "    ${ARCHIVE_PATH} \\"
echo "    your-new-domain.com \\"
echo "    /home/admin/platform-backend"
echo ""

warn "Не забудьте после миграции обновить webhook URL в YooKassa!"
warn "И удалите архив с обоих серверов после успешного переноса."
