"""Хранилище токенов входа в веб-кабинет (таблица `web_login_tokens`).

Прямой asyncpg без CacheService: токены живут минуты, кеш им не нужен.
Все переходы статуса — условные UPDATE, поэтому два параллельных
подтверждения или опроса не выдадут две сессии.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

import asyncpg


@dataclass(frozen=True)
class WebLoginTokenRow:
    status: str  # pending | confirmed | used
    tg_id: Optional[int]
    landing_uid: Optional[str]
    expires_at: datetime


class WebLoginTokenStore(Protocol):
    async def create(
        self, *, token_hash: str, poll_hash: str, landing_uid: Optional[str], ttl_seconds: int
    ) -> None: ...

    async def confirm(self, *, token_hash: str, tg_id: int) -> Optional[WebLoginTokenRow]:
        """pending → confirmed, только пока токен не истёк. ``None`` — не подошёл."""

    async def get_by_token(self, token_hash: str) -> Optional[WebLoginTokenRow]: ...

    async def consume(self, *, poll_hash: str) -> Optional[int]:
        """confirmed → used, только пока не истёк. Возвращает ``tg_id`` или ``None``."""

    async def get_by_poll(self, poll_hash: str) -> Optional[WebLoginTokenRow]: ...


_COLUMNS = "status, tg_id, landing_uid, expires_at"


def _row(record: Optional[asyncpg.Record]) -> Optional[WebLoginTokenRow]:
    if record is None:
        return None
    return WebLoginTokenRow(
        status=record["status"],
        tg_id=record["tg_id"],
        landing_uid=record["landing_uid"],
        expires_at=record["expires_at"],
    )


class PostgresWebLoginTokenStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create(
        self, *, token_hash: str, poll_hash: str, landing_uid: Optional[str], ttl_seconds: int
    ) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Попутно чистим старые токены, чтобы таблица не росла.
                await conn.execute(
                    "DELETE FROM web_login_tokens WHERE expires_at < NOW() - INTERVAL '1 day'"
                )
                await conn.execute(
                    """
                    INSERT INTO web_login_tokens (token_hash, poll_hash, landing_uid, expires_at)
                    VALUES ($1, $2, $3, NOW() + make_interval(secs => $4))
                    """,
                    token_hash,
                    poll_hash,
                    landing_uid,
                    float(ttl_seconds),
                )

    async def confirm(self, *, token_hash: str, tg_id: int) -> Optional[WebLoginTokenRow]:
        record = await self._pool.fetchrow(
            f"""
            UPDATE web_login_tokens
               SET status = 'confirmed', tg_id = $2, confirmed_at = NOW()
             WHERE token_hash = $1 AND status = 'pending' AND expires_at > NOW()
            RETURNING {_COLUMNS}
            """,
            token_hash,
            tg_id,
        )
        return _row(record)

    async def get_by_token(self, token_hash: str) -> Optional[WebLoginTokenRow]:
        record = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM web_login_tokens WHERE token_hash = $1", token_hash
        )
        return _row(record)

    async def consume(self, *, poll_hash: str) -> Optional[int]:
        return await self._pool.fetchval(
            """
            UPDATE web_login_tokens
               SET status = 'used'
             WHERE poll_hash = $1 AND status = 'confirmed' AND expires_at > NOW()
            RETURNING tg_id
            """,
            poll_hash,
        )

    async def get_by_poll(self, poll_hash: str) -> Optional[WebLoginTokenRow]:
        record = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM web_login_tokens WHERE poll_hash = $1", poll_hash
        )
        return _row(record)
