#!/usr/bin/env bash
# Initialize a fresh test database for the VPN platform.
#
# Usage:
#   ./tools/db/init_test_db.sh
#
# Behavior:
#   1. Reads DATABASE_URL from the root .env file.
#   2. Connects to the 'postgres' maintenance DB.
#   3. Drops & creates a test DB named {original_db}_test.
#   4. Applies test_schema.sql to the new test DB.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_ROOT/.env"
SCHEMA_FILE="$PROJECT_ROOT/test_schema.sql"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: .env not found at $ENV_FILE"
    exit 1
fi

# Extract DATABASE_URL from .env
DATABASE_URL=$(grep '^DATABASE_URL=' "$ENV_FILE" | cut -d '=' -f2-)
# Trim surrounding quotes if present
DATABASE_URL=$(echo "$DATABASE_URL" | sed "s/^[[:space:]]*['\\\"]//;s/['\\\"][[:space:]]*$//")
if [[ -z "$DATABASE_URL" ]]; then
    echo "ERROR: DATABASE_URL not found in $ENV_FILE"
    exit 1
fi

# Parse DSN components
# Format: postgresql://user:pass@host:port/dbname
BASE_URL=$(echo "$DATABASE_URL" | sed -E 's|^(postgresql://[^:]+:[^@]+@[^/]+)/.*|\1|')
DB_NAME=$(echo "$DATABASE_URL" | sed -E 's|^.+/||')
TEST_DB_NAME="${DB_NAME}_test"
MAINTENANCE_DSN="${BASE_URL}/postgres"
TEST_DSN="${BASE_URL}/${TEST_DB_NAME}"

echo "Original DB DSN:  $DATABASE_URL"
echo "Maintenance DSN:  $MAINTENANCE_DSN"
echo "Test DB name:     $TEST_DB_NAME"
echo "Test DB DSN:      $TEST_DSN"

# Terminate existing connections, drop and recreate test DB
psql "$MAINTENANCE_DSN" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$TEST_DB_NAME' AND pid <> pg_backend_pid();" > /dev/null 2>&1 || true
psql "$MAINTENANCE_DSN" -c "DROP DATABASE IF EXISTS \"$TEST_DB_NAME\";"
psql "$MAINTENANCE_DSN" -c "CREATE DATABASE \"$TEST_DB_NAME\";"
echo "Database '$TEST_DB_NAME' created."

# Apply schema
psql "$TEST_DSN" -f "$SCHEMA_FILE"
echo "Schema applied from $SCHEMA_FILE"

echo ""
echo "Test database is ready."
echo "  DATABASE_URL (test): $TEST_DSN"
