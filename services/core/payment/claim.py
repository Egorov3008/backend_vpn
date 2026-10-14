"""Эксклюзивный захват платежа на время обработки.

Один и тот же платёж обрабатывают три пути: webhook ЮKassa, проверка статуса
(`GET /payments/{id}/status`, её каждые несколько секунд дёргает Mini App) и
pending-sweep. Без захвата они пересекаются, пока создаётся ключ в панели, и
оба видят ``pending`` — пользователь получает два ключа за один платёж.

Захват — сессионный advisory lock Postgres на отдельном соединении из пула:
работает между процессами (sweep-скрипт, несколько воркеров), а при падении
процесса или обрыве соединения снимается сам, и платёж остаётся ``pending``
для повторной обработки.
"""
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

import asyncpg

from logger import logger

_LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended($1, 0))"
_UNLOCK_SQL = "SELECT pg_advisory_unlock(hashtextextended($1, 0))"
_STATUS_SQL = "SELECT status FROM payments WHERE payment_id = $1"


@dataclass(frozen=True)
class PaymentClaimResult:
    acquired: bool
    # Статус платежа в БД, прочитанный уже под захватом (кеш процесса может
    # отставать от записи, сделанной другим процессом). None — если захват
    # не получен или платежа в БД нет.
    db_status: str | None = None


class PaymentClaim(Protocol):
    def hold(self, payment_id: str) -> AbstractAsyncContextManager[PaymentClaimResult]: ...


class PgAdvisoryPaymentClaim:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @asynccontextmanager
    async def hold(self, payment_id: str) -> AsyncIterator[PaymentClaimResult]:
        lock_key = f"payment:{payment_id}"
        async with self._pool.acquire() as conn:
            if not await conn.fetchval(_LOCK_SQL, lock_key):
                yield PaymentClaimResult(acquired=False)
                return
            try:
                status = await conn.fetchval(_STATUS_SQL, payment_id)
                yield PaymentClaimResult(acquired=True, db_status=status)
            finally:
                try:
                    await conn.execute(_UNLOCK_SQL, lock_key)
                except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as e:
                    # asyncpg при возврате соединения в пул всё равно делает
                    # pg_advisory_unlock_all(), так что лок не повиснет.
                    logger.warning("Не удалось снять захват платежа", payment_id=payment_id, error=str(e))
