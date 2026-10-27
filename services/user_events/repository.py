"""Хранение событий в Postgres (таблица user_events, миграция 20261029_user_events.sql)."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol

import asyncpg


@dataclass(frozen=True)
class UserEvent:
    name: str
    source: str
    tg_id: Optional[int] = None
    anon_id: Optional[str] = None
    props: Dict[str, Any] = field(default_factory=dict)


class EventSink(Protocol):
    """Куда пишутся события. Трекер зависит только от этого."""

    async def write(self, event: UserEvent) -> None: ...


class UserEventRepository:
    """``EventSink`` на Postgres плюс чтение для админки и очистка старых строк."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def write(self, event: UserEvent) -> None:
        await self._pool.execute(
            "INSERT INTO user_events (tg_id, anon_id, name, source, props) VALUES ($1, $2, $3, $4, $5::jsonb)",
            event.tg_id,
            event.anon_id,
            event.name,
            event.source,
            json.dumps(event.props, ensure_ascii=False),
        )

    async def for_user(self, tg_id: int, limit: int) -> List[Dict[str, Any]]:
        """Последние события пользователя, новые первыми."""
        rows = await self._pool.fetch(
            "SELECT name, source, props, created_at FROM user_events"
            " WHERE tg_id = $1 ORDER BY created_at DESC, id DESC LIMIT $2",
            tg_id,
            limit,
        )
        return [
            {
                "name": r["name"],
                "source": r["source"],
                "props": json.loads(r["props"]) if isinstance(r["props"], str) else dict(r["props"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    async def purge_before(self, moment: datetime) -> int:
        result = await self._pool.execute("DELETE FROM user_events WHERE created_at < $1", moment)
        return int(result.split()[-1])
