"""Многошаговое состояние воронок уведомлений (таблица cache)."""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg

MAX_STEPS = 3


class StepState:
    """Хранит прогресс многошаговой воронки для одного пользователя.

    value = {"step": <int>, "last_sent_ms": <int>, "key_email": <str>}
    либо терминальное {"step": MAX_STEPS, "terminal": True}.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def _key(self, funnel_id: str, tg_id: int) -> str:
        return f"notif_{funnel_id}_{tg_id}"

    async def get_state(self, funnel_id: str, tg_id: int) -> Optional[dict]:
        key = self._key(funnel_id, tg_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM cache WHERE key = $1 AND (expires_at IS NULL OR expires_at > NOW())",
                key,
            )
        if not row:
            return None
        value = row["value"]
        return dict(value) if value is not None else None

    async def advance_step(
        self,
        funnel_id: str,
        tg_id: int,
        last_sent_ms: int,
        key_email: str,
        ttl: timedelta,
    ) -> int:
        key = self._key(funnel_id, tg_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM cache WHERE key = $1", key)
            current = dict(row["value"]) if row and row["value"] is not None else {}
            new_step = int(current.get("step", 0)) + 1
            value = {"step": new_step, "last_sent_ms": last_sent_ms, "key_email": key_email}
            expires_at = datetime.now(timezone.utc) + ttl
            await conn.execute(
                """
                INSERT INTO cache (key, value, expires_at)
                VALUES ($1, $2::jsonb, $3)
                ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, expires_at = $3
                """,
                key,
                json.dumps(value),
                expires_at,
            )
        return new_step

    async def mark_terminal(self, funnel_id: str, tg_id: int, ttl: timedelta) -> None:
        key = self._key(funnel_id, tg_id)
        value = {"step": MAX_STEPS, "terminal": True}
        expires_at = datetime.now(timezone.utc) + ttl
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cache (key, value, expires_at)
                VALUES ($1, $2::jsonb, $3)
                ON CONFLICT (key) DO UPDATE SET value = $2::jsonb, expires_at = $3
                """,
                key,
                json.dumps(value),
                expires_at,
            )