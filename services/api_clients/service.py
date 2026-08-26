"""Управление API-ключами внешних потребителей публичного API (Этап 4).

Раздача/ротация/отзыв — admin-only операции (см. api/v1/admin_api_clients.py).
Прямой asyncpg, без CacheService — см. docstring ApiClient.
"""
import hashlib
import secrets
from typing import List, Optional

import asyncpg

from models.api_clients.api_client import ApiClient

_KEY_PREFIX = "pub_"


def _generate_raw_key() -> str:
    return f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _display_prefix(raw_key: str) -> str:
    # Достаточно для опознания ключа в admin-панели/логах, не позволяет
    # восстановить сам ключ (короче, чем длина token_urlsafe(32)).
    return raw_key[: len(_KEY_PREFIX) + 8]


class ApiClientService:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def create(self, name: str, scopes: List[str]) -> tuple[ApiClient, str]:
        raw_key = _generate_raw_key()
        row = await self._pool.fetchrow(
            """
            INSERT INTO api_clients (name, key_prefix, key_hash, scopes)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, key_prefix, key_hash, scopes, is_active,
                      created_at, last_used_at, revoked_at
            """,
            name,
            _display_prefix(raw_key),
            _hash_key(raw_key),
            scopes,
        )
        return ApiClient(**dict(row)), raw_key

    async def list_all(self) -> List[ApiClient]:
        rows = await self._pool.fetch(
            """
            SELECT id, name, key_prefix, key_hash, scopes, is_active,
                   created_at, last_used_at, revoked_at
            FROM api_clients
            ORDER BY id
            """
        )
        return [ApiClient(**dict(row)) for row in rows]

    async def revoke(self, client_id: int) -> Optional[ApiClient]:
        row = await self._pool.fetchrow(
            """
            UPDATE api_clients
            SET is_active = FALSE, revoked_at = NOW()
            WHERE id = $1
            RETURNING id, name, key_prefix, key_hash, scopes, is_active,
                      created_at, last_used_at, revoked_at
            """,
            client_id,
        )
        return ApiClient(**dict(row)) if row else None

    async def rotate(self, client_id: int) -> Optional[tuple[ApiClient, str]]:
        """Выпускает новый ключ для существующего client_id, реактивирует
        клиента (если был revoked) и обнуляет старый ключ — старый перестаёт
        работать немедленно."""
        raw_key = _generate_raw_key()
        row = await self._pool.fetchrow(
            """
            UPDATE api_clients
            SET key_prefix = $2, key_hash = $3, is_active = TRUE, revoked_at = NULL
            WHERE id = $1
            RETURNING id, name, key_prefix, key_hash, scopes, is_active,
                      created_at, last_used_at, revoked_at
            """,
            client_id,
            _display_prefix(raw_key),
            _hash_key(raw_key),
        )
        if not row:
            return None
        return ApiClient(**dict(row)), raw_key

    async def verify(self, raw_key: str) -> Optional[ApiClient]:
        key_hash = _hash_key(raw_key)
        row = await self._pool.fetchrow(
            """
            SELECT id, name, key_prefix, key_hash, scopes, is_active,
                   created_at, last_used_at, revoked_at
            FROM api_clients
            WHERE key_hash = $1 AND is_active = TRUE
            """,
            key_hash,
        )
        if not row:
            return None
        # Best-effort — не блокируем auth-путь на ошибке этого UPDATE.
        try:
            await self._pool.execute(
                "UPDATE api_clients SET last_used_at = NOW() WHERE id = $1", row["id"]
            )
        except Exception:
            pass
        return ApiClient(**dict(row))
