#!/usr/bin/env bash
set -euo pipefail

# ===================================================================
# VPN Platform — восстановление .env + PostgreSQL на новом сервере
#
# Предполагается, что код проекта уже развёрнут (git clone / pull сделан).
#
# Использование:
#   bash tools/db/restore_db_and_env.sh ARCHIVE_PATH [PROJECT_DIR]
#
# Пример:
#   bash tools/db/restore_db_and_env.sh \
#     /opt/vpn-db-env-20260620-120000.tar.gz \
#     /home/admin/platform-backend
# ===================================================================

ARCHIVE_PATH="${1:?Укажите путь к архиву (vpn-db-env-*.tar.gz)}"
PROJECT_DIR="${2:-/home/admin/platform-backend}"

WORK_DIR="$(mktemp -d)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[RESTORE]${NC} $*"; }
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
    err "Docker не найден."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    err "Docker Compose plugin не найден."
    exit 1
fi

if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]]; then
    err "В ${PROJECT_DIR} не найден docker-compose.yml. Убедитесь, что проект развёрнут."
    exit 1
fi

# -------------------------------------------------------------------
# Безопасно читаем DB-переменные из .env
# -------------------------------------------------------------------
read_env_var() {
    local file="$1" key="$2"
    grep -E "^${key}=" "${file}" 2>/dev/null | head -n1 | cut -d'=' -f2- | sed -E 's/^[[:space:]]*//;s/[[:space:]]*$//;s/^["\x27]|["\x27]$//g'
}

# -------------------------------------------------------------------
# Распаковка архива
# -------------------------------------------------------------------
log "Распаковываю архив..."
tar -xzf "${ARCHIVE_PATH}" -C "${WORK_DIR}"

DATA_DIR=$(find "${WORK_DIR}" -maxdepth 1 -type d -name 'vpn-db-env-*' | head -n 1)
if [[ -z "${DATA_DIR}" ]]; then
    err "Не удалось найти директорию vpn-db-env-* после распаковки"
    exit 1
fi

if [[ ! -f "${DATA_DIR}/env/.env" ]]; then
    err "В архиве не найден .env"
    exit 1
fi

if [[ ! -f "${DATA_DIR}/db_backup/"*.custom.dump ]]; then
    err "В архиве не найден дамп БД (*.custom.dump)"
    exit 1
fi

DUMP_FILE=$(find "${DATA_DIR}/db_backup" -name '*.custom.dump' | head -n 1)

# -------------------------------------------------------------------
# Копируем .env
# -------------------------------------------------------------------
log "Копирую .env в ${PROJECT_DIR}..."
cp -f "${DATA_DIR}/env/.env" "${PROJECT_DIR}/.env"

# Загружаем DB-переменные
DB_NAME=$(read_env_var "${PROJECT_DIR}/.env" DB_NAME)
DB_USER=$(read_env_var "${PROJECT_DIR}/.env" DB_USER)
DB_PASSWORD=$(read_env_var "${PROJECT_DIR}/.env" DB_PASSWORD)

if [[ -z "${DB_NAME}" || -z "${DB_USER}" || -z "${DB_PASSWORD}" ]]; then
    err "В .env не заданы DB_NAME / DB_USER / DB_PASSWORD"
    exit 1
fi

log "БД: ${DB_NAME}, пользователь: ${DB_USER}"

# -------------------------------------------------------------------
# Запуск PostgreSQL
# -------------------------------------------------------------------
log "Запускаю PostgreSQL..."
(
    cd "${PROJECT_DIR}"
    docker compose up -d postgres
)

POSTGRES_CONTAINER=""
for i in {1..60}; do
    POSTGRES_CONTAINER=$(docker compose -f "${PROJECT_DIR}/docker-compose.yml" ps -q postgres 2>/dev/null || true)
    if [[ -n "${POSTGRES_CONTAINER}" ]] && docker exec "${POSTGRES_CONTAINER}" pg_isready -U "${DB_USER}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if [[ -z "${POSTGRES_CONTAINER}" ]]; then
    err "PostgreSQL не запустился за 60 секунд"
    exit 1
fi

# -------------------------------------------------------------------
# Восстановление БД
# -------------------------------------------------------------------
log "Восстанавливаю базу ${DB_NAME}..."

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

# Очищаем public schema
docker exec -i "${POSTGRES_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" > /dev/null 2>&1 || true

# Копируем дамп внутрь контейнера и восстанавливаем
DUMP_BASENAME=$(basename "${DUMP_FILE}")
docker cp "${DUMP_FILE}" "${POSTGRES_CONTAINER}:/tmp/${DUMP_BASENAME}"

docker exec -e PGPASSWORD="${DB_PASSWORD}" "${POSTGRES_CONTAINER}" \
    pg_restore -U "${DB_USER}" -d "${DB_NAME}" --no-owner --no-privileges --clean --if-exists \
    "/tmp/${DUMP_BASENAME}" || warn "pg_restore завершился с предупреждениями (обычно нормально)"

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
for f in $(ls "${PROJECT_DIR}/web/migrations/"*.sql 2>/dev/null | sort); do
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
# Перезапуск всех сервисов
# -------------------------------------------------------------------
log "Пересобираю и перезапускаю сервисы..."
(
    cd "${PROJECT_DIR}"
    docker compose down
    docker compose up --build -d
)

# -------------------------------------------------------------------
# Health check
# -------------------------------------------------------------------
log "Жду healthcheck backend..."
HEALTHY=false
for i in {1..60}; do
    if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1 || curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        HEALTHY=true
        break
    fi
    sleep 2
done

if [[ "${HEALTHY}" != true ]]; then
    warn "Healthcheck backend не прошёл. Смотрите логи:"
    warn "  cd ${PROJECT_DIR} && docker compose logs -f"
else
    log "Backend отвечает на /health"
fi

log "Статус сервисов:"
(
    cd "${PROJECT_DIR}"
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
log "Восстановление завершено!"
echo ""
info "Проект:     ${PROJECT_DIR}"
info "БД:         ${DB_NAME}"
info "Таблиц:     ${TABLE_COUNT}"
echo ""
warn "Обязательно выполните после восстановления:"
echo "  1. Проверьте, что в .env правильный домен и WEBHOOK_BASE_URL"
echo "  2. Обновите webhook URL в YooKassa"
echo "  3. Проверьте доступность сайта и работу бота"
echo "  4. Проверьте подключение к 3x-UI: XUI_API_URL=$(read_env_var "${PROJECT_DIR}/.env" XUI_API_URL)"
echo "  5. Удалите архив восстановления с обоих серверов"
echo ""
