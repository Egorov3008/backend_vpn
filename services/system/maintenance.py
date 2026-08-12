"""Ручной режим профилактики панели 3x-ui (таблица cache)."""

import json
from datetime import datetime, timezone
from typing import Optional

import asyncpg


class PanelMaintenanceError(Exception):
    """Операция заблокирована — админ включил режим профилактики панели."""


class MaintenanceModeService:
    """Хранит флаг режима профилактики в общей таблице cache.

    value = {"enabled": bool, "reason": str|None, "enabled_at": iso|None, "enabled_by": int|None}
    expires_at = NULL — флаг постоянный, снимается только явным set(enabled=False).
    """

    CACHE_KEY = "maintenance_mode"

    async def get_status(self, pool: asyncpg.Pool) -> dict:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM cache WHERE key = $1", self.CACHE_KEY
            )
        if not row or row["value"] is None:
            return {"enabled": False, "reason": None, "enabled_at": None, "enabled_by": None}
        value = row["value"]
        parsed = json.loads(value) if isinstance(value, str) else dict(value)
        parsed.setdefault("enabled", False)
        parsed.setdefault("reason", None)
        parsed.setdefault("enabled_at", None)
        parsed.setdefault("enabled_by", None)
        return parsed

    async def is_enabled(self, pool: asyncpg.Pool) -> bool:
        status = await self.get_status(pool)
        return bool(status.get("enabled"))

    async def set(
        self,
        pool: asyncpg.Pool,
        enabled: bool,
        reason: Optional[str] = None,
        admin_tg_id: Optional[int] = None,
    ) -> dict:
        value = {
            "enabled": enabled,
            "reason": reason if enabled else None,
            "enabled_at": datetime.now(timezone.utc).isoformat() if enabled else None,
            "enabled_by": admin_tg_id if enabled else None,
        }
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cache (key, value, expires_at)
                VALUES ($1, $2::jsonb, NULL)
                ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, expires_at = NULL
                """,
                self.CACHE_KEY,
                json.dumps(value),
            )
        return value


maintenance_mode = MaintenanceModeService()
