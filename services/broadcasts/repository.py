"""SQL рассылок (asyncpg): broadcasts + снимок получателей broadcast_recipients."""
from typing import Any, Optional, Sequence

import asyncpg

from services.broadcasts.audiences import Audience, recipients_sql
from services.broadcasts.models import (
    ACTIVE_STATUSES,
    Broadcast,
    BroadcastMessage,
    BroadcastStatus,
    Button,
    DeliveryStatus,
    Media,
    MediaType,
    Progress,
)

# Ключ advisory-блокировки: две рассылки одновременно не запускаются.
_CREATE_LOCK = 20261027

_PROGRESS = """
    SELECT broadcast_id,
           COUNT(*) AS total,
           COUNT(*) FILTER (WHERE status IN ('pending', 'sending')) AS pending,
           COUNT(*) FILTER (WHERE status = 'sent') AS sent,
           COUNT(*) FILTER (WHERE status = 'blocked') AS blocked,
           COUNT(*) FILTER (WHERE status = 'failed') AS failed
    FROM broadcast_recipients
    WHERE broadcast_id = ANY($1::int[])
    GROUP BY broadcast_id
"""


class BroadcastAlreadyRunning(Exception):
    def __init__(self, broadcast_id: int):
        super().__init__(f"broadcast {broadcast_id} is running")
        self.broadcast_id = broadcast_id


def _message(row: Any) -> BroadcastMessage:
    media = None
    if row["media_type"] and row["media_file_id"]:
        media = Media(MediaType(row["media_type"]), row["media_file_id"], row["media_file_name"])
    button = None
    if row["button_kind"]:
        button = Button(row["button_kind"], row["button_text"] or "", row["button_url"] or "")
    return BroadcastMessage(
        text=row["text"] or "",
        media=media,
        button=button,
        disable_notification=row["disable_notification"],
        pin=row["pin"],
    )


def _broadcast(row: Any, progress: Optional[Progress]) -> Broadcast:
    return Broadcast(
        id=row["id"],
        admin_tg_id=row["admin_tg_id"],
        audience=row["audience"],
        message=_message(row),
        status=BroadcastStatus(row["status"]),
        error=row["error"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        progress=progress or Progress(),
    )


class BroadcastRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    # --- аудитория ----------------------------------------------------------

    async def count_audience(self, audience: Audience, exclude_tg_id: Optional[int]) -> int:
        return int(await self._pool.fetchval(f"SELECT COUNT(*) FROM ({recipients_sql(audience)}) r", exclude_tg_id))

    # --- создание и чтение ----------------------------------------------------

    async def create(self, admin_tg_id: Optional[int], audience: Audience, message: BroadcastMessage) -> int:
        """Рассылка + снимок получателей одной транзакцией. Админ в получатели не входит:
        ему уходит контрольная копия до запуска."""
        media, button = message.media, message.button
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _CREATE_LOCK)
            running = await conn.fetchval(
                "SELECT id FROM broadcasts WHERE status = ANY($1::text[]) ORDER BY id LIMIT 1", list(ACTIVE_STATUSES)
            )
            if running is not None:
                raise BroadcastAlreadyRunning(running)
            broadcast_id = await conn.fetchval(
                """
                INSERT INTO broadcasts (admin_tg_id, audience, text, media_type, media_file_id, media_file_name,
                                        button_kind, button_text, button_url, disable_notification, pin)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
                """,
                admin_tg_id, audience.id, message.text,
                media.type.value if media else None, media.file_id if media else None,
                media.file_name if media else None,
                button.kind if button else None, button.text if button else None, button.url if button else None,
                message.disable_notification, message.pin,
            )
            await conn.execute(
                "INSERT INTO broadcast_recipients (broadcast_id, tg_id)"
                f" SELECT $2::int, r.tg_id FROM ({recipients_sql(audience)}) r",
                admin_tg_id, broadcast_id,
            )
        return broadcast_id

    async def _progress(self, ids: Sequence[int]) -> dict[int, Progress]:
        if not ids:
            return {}
        rows = await self._pool.fetch(_PROGRESS, list(ids))
        return {
            r["broadcast_id"]: Progress(
                total=r["total"], pending=r["pending"], sent=r["sent"], blocked=r["blocked"], failed=r["failed"]
            )
            for r in rows
        }

    async def get(self, broadcast_id: int) -> Optional[Broadcast]:
        row = await self._pool.fetchrow("SELECT * FROM broadcasts WHERE id = $1", broadcast_id)
        if row is None:
            return None
        return _broadcast(row, (await self._progress([broadcast_id])).get(broadcast_id))

    async def page(self, limit: int, offset: int) -> tuple[list[Broadcast], int]:
        total = int(await self._pool.fetchval("SELECT COUNT(*) FROM broadcasts"))
        rows = await self._pool.fetch("SELECT * FROM broadcasts ORDER BY id DESC LIMIT $1 OFFSET $2", limit, offset)
        progress = await self._progress([r["id"] for r in rows])
        return [_broadcast(r, progress.get(r["id"])) for r in rows], total

    async def active_ids(self) -> list[int]:
        rows = await self._pool.fetch(
            "SELECT id FROM broadcasts WHERE status = ANY($1::text[]) ORDER BY id", list(ACTIVE_STATUSES)
        )
        return [r["id"] for r in rows]

    async def status(self, broadcast_id: int) -> Optional[BroadcastStatus]:
        value = await self._pool.fetchval("SELECT status FROM broadcasts WHERE id = $1", broadcast_id)
        return BroadcastStatus(value) if value else None

    # --- ход рассылки ---------------------------------------------------------

    async def claim(self, broadcast_id: int, limit: int) -> list[int]:
        """Берёт следующую пачку получателей (pending → sending). SKIP LOCKED: даже два
        процесса не возьмут одного человека дважды."""
        rows = await self._pool.fetch(
            """
            UPDATE broadcast_recipients r SET status = 'sending', updated_at = NOW()
            WHERE r.broadcast_id = $1 AND r.tg_id IN (
                SELECT tg_id FROM broadcast_recipients
                WHERE broadcast_id = $1 AND status = 'pending'
                ORDER BY tg_id LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            RETURNING r.tg_id
            """,
            broadcast_id, limit,
        )
        return sorted(r["tg_id"] for r in rows)

    async def record(self, broadcast_id: int, tg_id: int, status: DeliveryStatus, error: Optional[str]) -> None:
        await self._pool.execute(
            "UPDATE broadcast_recipients SET status = $3, error = $4, updated_at = NOW()"
            " WHERE broadcast_id = $1 AND tg_id = $2",
            broadcast_id, tg_id, status.value, error,
        )

    async def release_claimed(self, broadcast_id: int, tg_ids: Sequence[int] = ()) -> None:
        """Вернуть взятых, но не отправленных в очередь: после остановки процесса
        (все sending) или при отмене посреди пачки (переданные tg_ids)."""
        if tg_ids:
            await self._pool.execute(
                "UPDATE broadcast_recipients SET status = 'pending' WHERE broadcast_id = $1"
                " AND status = 'sending' AND tg_id = ANY($2::bigint[])",
                broadcast_id, list(tg_ids),
            )
        else:
            await self._pool.execute(
                "UPDATE broadcast_recipients SET status = 'pending' WHERE broadcast_id = $1 AND status = 'sending'",
                broadcast_id,
            )

    async def request_cancel(self, broadcast_id: int) -> bool:
        result = await self._pool.execute(
            "UPDATE broadcasts SET status = 'cancel_requested' WHERE id = $1 AND status = 'running'", broadcast_id
        )
        return result.endswith(" 1")

    async def finish(self, broadcast_id: int, status: BroadcastStatus, error: Optional[str] = None) -> None:
        await self._pool.execute(
            "UPDATE broadcasts SET status = $2, error = $3, finished_at = NOW()"
            " WHERE id = $1 AND status = ANY($4::text[])",
            broadcast_id, status.value, error, list(ACTIVE_STATUSES),
        )
