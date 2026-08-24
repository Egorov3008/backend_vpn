#!/usr/bin/env python3
"""
Initialize a fresh test database for the VPN platform.

Usage:
    python scripts/init_test_db.py

Behavior:
    1. Reads DATABASE_URL from the root .env file.
    2. Connects to the 'postgres' maintenance DB.
    3. Drops & creates a test DB named {original_db}_test.
    4. Applies test_schema.sql to the new test DB.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
SCHEMA_PATH = PROJECT_ROOT / "test_schema.sql"


def load_env(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def derive_test_dsn(original_dsn: str) -> tuple[str, str]:
    """Return (maintenance_dsn, test_dsn, test_db_name)."""
    # Example: postgresql://user:pass@host:port/dbname
    match = re.match(r"^(postgresql://[^:]+:[^@]+@[^/]+)/(\w+)$", original_dsn)
    if not match:
        raise ValueError(f"Cannot parse DATABASE_URL: {original_dsn}")
    base = match.group(1)
    db_name = match.group(2)
    test_db_name = f"{db_name}_test"
    maintenance_dsn = f"{base}/postgres"
    test_dsn = f"{base}/{test_db_name}"
    return maintenance_dsn, test_dsn, test_db_name


async def main() -> int:
    env = load_env(ENV_PATH)
    original_dsn = env.get("DATABASE_URL")
    if not original_dsn:
        print(f"ERROR: DATABASE_URL not found in {ENV_PATH}")
        return 1

    maintenance_dsn, test_dsn, test_db_name = derive_test_dsn(original_dsn)

    print(f"Original DB DSN:  {original_dsn}")
    print(f"Maintenance DSN:  {maintenance_dsn}")
    print(f"Test DB name:     {test_db_name}")
    print(f"Test DB DSN:      {test_dsn}")

    # Read schema SQL
    if not SCHEMA_PATH.exists():
        print(f"ERROR: Schema file not found: {SCHEMA_PATH}")
        return 1
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    # Connect to maintenance DB to create/drop test DB
    conn = await asyncpg.connect(maintenance_dsn)
    try:
        # Terminate existing connections to the test DB
        await conn.execute(
            f"""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = '{test_db_name}'
              AND pid <> pg_backend_pid()
            """
        )
        # Drop if exists
        await conn.execute(f'DROP DATABASE IF EXISTS "{test_db_name}"')
        # Create fresh test DB
        await conn.execute(f'CREATE DATABASE "{test_db_name}"')
        print(f"Database '{test_db_name}' created.")
    finally:
        await conn.close()

    # Apply schema to the new test DB
    conn = await asyncpg.connect(test_dsn)
    try:
        await conn.execute(schema_sql)
        print(f"Schema applied from {SCHEMA_PATH}")
    finally:
        await conn.close()

    print("\nTest database is ready.")
    print(f"  DATABASE_URL (test): {test_dsn}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
