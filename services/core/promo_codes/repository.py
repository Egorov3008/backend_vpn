"""SQL промокодов (asyncpg)."""
from datetime import datetime
from typing import Any, Optional, Sequence

import asyncpg

from services.core.promo_codes.models import PromoCode, PromoRequest, PromoStats, PromoUsage
from services.core.promo_codes.rules import PromoRule, Reason, first_failure

_PROMO_COLUMNS = (
    "id, code, discount_percent, starts_at, ends_at, max_redemptions, audience, operations,"
    " max_months, owner_tg_id, is_active, created_by, created_at"
)


class DuplicatePromoCode(Exception):
    """Код с таким текстом (без учёта регистра) уже есть."""


def _promo(row: Any) -> Optional[PromoCode]:
    return PromoCode(**dict(row)) if row is not None else None


class ConnectionFacts:
    """``PromoFacts`` поверх соединения: внутри транзакции резерва видны её же блокировки."""

    def __init__(self, conn, reservation_ttl_minutes: int):
        self._conn = conn
        self._ttl = reservation_ttl_minutes

    async def has_paid(self, tg_id: int) -> bool:
        return bool(await self._conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM payments WHERE tg_id = $1::bigint AND status = 'succeeded')",
            tg_id,
        ))

    async def user_has_used(self, promo_id: int, tg_id: int) -> bool:
        return bool(await self._conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM promo_redemptions"
            " WHERE promo_id = $1 AND tg_id = $2::bigint AND status = 'used')",
            promo_id, tg_id,
        ))

    async def taken_by_others(self, promo_id: int, tg_id: int) -> int:
        return int(await self._conn.fetchval(
            """
            SELECT COUNT(*) FROM promo_redemptions
            WHERE promo_id = $1 AND tg_id <> $2::bigint
              AND (status = 'used' OR reserved_at > NOW() - make_interval(mins => $3))
            """,
            promo_id, tg_id, self._ttl,
        ))


class PromoCodeRepository:
    def __init__(self, pool: asyncpg.Pool, reservation_ttl_minutes: int):
        self._pool = pool
        self._ttl = reservation_ttl_minutes

    # --- чтение -----------------------------------------------------------

    async def get_by_code(self, code: str) -> Optional[PromoCode]:
        row = await self._pool.fetchrow(
            f"SELECT {_PROMO_COLUMNS} FROM promo_codes WHERE upper(code) = upper($1)", code
        )
        return _promo(row)

    async def get(self, promo_id: int) -> Optional[PromoCode]:
        row = await self._pool.fetchrow(f"SELECT {_PROMO_COLUMNS} FROM promo_codes WHERE id = $1", promo_id)
        return _promo(row)

    def facts(self, conn=None) -> ConnectionFacts:
        return ConnectionFacts(conn or self._pool, self._ttl)

    async def list_with_usage(self, limit: int, offset: int) -> tuple[list[tuple[PromoCode, PromoUsage]], int]:
        """Личные коды (owner_tg_id) в общий список не попадают: их тысячи, и это не акции."""
        rows = await self._pool.fetch(
            f"""
            SELECT {", ".join("c." + c.strip() for c in _PROMO_COLUMNS.split(","))},
                   COUNT(r.id) FILTER (WHERE r.status = 'used') AS used,
                   COUNT(r.id) FILTER (WHERE r.status = 'used' AND r.was_new_user) AS used_new_users,
                   COUNT(r.id) FILTER (
                       WHERE r.status = 'reserved' AND r.reserved_at > NOW() - make_interval(mins => $3)
                   ) AS reserved
            FROM promo_codes c
            LEFT JOIN promo_redemptions r ON r.promo_id = c.id
            WHERE c.owner_tg_id IS NULL
            GROUP BY c.id
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset, self._ttl,
        )
        total = await self._pool.fetchval("SELECT COUNT(*) FROM promo_codes WHERE owner_tg_id IS NULL")
        items = []
        for row in rows:
            data = dict(row)
            usage = PromoUsage(
                used=data.pop("used"), used_new_users=data.pop("used_new_users"), reserved=data.pop("reserved")
            )
            items.append((PromoCode(**data), usage))
        return items, int(total)

    async def stats(self, promo: PromoCode, window_days: int) -> PromoStats:
        """Итоги акции. «До» и «после» — все успешные оплаты сервиса за ``window_days``
        дней до начала и после конца кода: видно, не сдвинулись ли оплаты на дни акции."""
        row = await self._pool.fetchrow(
            """
            WITH r AS (SELECT * FROM promo_redemptions WHERE promo_id = $1),
                 paid AS (
                     SELECT COALESCE(succeeded_at, created_at) AS at FROM payments WHERE status = 'succeeded'
                 )
            SELECT
                (SELECT COUNT(*) FROM r WHERE status = 'used') AS used,
                (SELECT COUNT(*) FROM r WHERE status = 'used' AND was_new_user) AS used_new_users,
                (SELECT COUNT(*) FROM r WHERE status = 'reserved'
                    AND reserved_at > NOW() - make_interval(mins => $5)) AS reserved_now,
                (SELECT COUNT(*) FROM r WHERE status = 'reserved'
                    AND reserved_at <= NOW() - make_interval(mins => $5)) AS abandoned,
                (SELECT COALESCE(SUM(amount), 0) FROM payments
                    WHERE promo_code_id = $1 AND status = 'succeeded') AS revenue,
                (SELECT COALESCE(SUM(promo_discount), 0) FROM payments
                    WHERE promo_code_id = $1 AND status = 'succeeded') AS discount_total,
                (SELECT COUNT(*) FROM paid
                    WHERE at >= $2::timestamptz - make_interval(days => $4) AND at < $2::timestamptz) AS payments_before,
                (SELECT COUNT(*) FROM paid WHERE at >= $2::timestamptz AND at < $3::timestamptz) AS payments_during,
                (SELECT COUNT(*) FROM paid
                    WHERE at >= $3::timestamptz AND at < $3::timestamptz + make_interval(days => $4)) AS payments_after,
                (SELECT COUNT(DISTINCT r.tg_id) FROM r
                    JOIN payments p ON p.tg_id = r.tg_id AND p.status = 'succeeded'
                     AND p.payment_id IS DISTINCT FROM r.payment_id
                     AND COALESCE(p.succeeded_at, p.created_at) > r.used_at
                    WHERE r.status = 'used') AS repeat_payers
            """,
            promo.id, promo.starts_at, promo.ends_at, window_days, self._ttl,
        )
        data = dict(row)
        return PromoStats(
            used=data["used"],
            used_new_users=data["used_new_users"],
            used_existing_users=data["used"] - data["used_new_users"],
            reserved_now=data["reserved_now"],
            abandoned=data["abandoned"],
            revenue=round(float(data["revenue"]), 2),
            discount_total=round(float(data["discount_total"]), 2),
            payments_before=data["payments_before"],
            payments_during=data["payments_during"],
            payments_after=data["payments_after"],
            repeat_payers=data["repeat_payers"],
            window_days=window_days,
        )

    # --- запись -----------------------------------------------------------

    async def create(self, **fields) -> PromoCode:
        columns = list(fields)
        placeholders = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
        try:
            row = await self._pool.fetchrow(
                f"INSERT INTO promo_codes ({', '.join(columns)}) VALUES ({placeholders})"
                f" RETURNING {_PROMO_COLUMNS}",
                *fields.values(),
            )
        except asyncpg.UniqueViolationError as e:
            raise DuplicatePromoCode(fields.get("code")) from e
        return _promo(row)

    async def set_active(self, promo_id: int, is_active: bool) -> Optional[PromoCode]:
        row = await self._pool.fetchrow(
            f"UPDATE promo_codes SET is_active = $2 WHERE id = $1 RETURNING {_PROMO_COLUMNS}",
            promo_id, is_active,
        )
        return _promo(row)

    async def reserve(
        self,
        promo_id: int,
        request: PromoRequest,
        discount_amount: float,
        rules: Sequence[PromoRule],
        now: datetime,
    ) -> Optional[str]:
        """Занять активацию кода под счёт. None — занято; иначе причина отказа.

        Строка кода блокируется (FOR UPDATE), поэтому параллельные резервы одного кода
        идут по очереди и лимит не превышается. Правила проверяются повторно уже под
        блокировкой: между расчётом цены и оплатой код могли выключить или выбрать до конца.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"SELECT {_PROMO_COLUMNS} FROM promo_codes WHERE id = $1 FOR UPDATE", promo_id
                )
                promo = _promo(row)
                if promo is None:
                    return Reason.NOT_FOUND
                facts = ConnectionFacts(conn, self._ttl)
                reason = await first_failure(rules, promo, request, facts, now)
                if reason is not None:
                    return reason
                was_new_user = not await facts.has_paid(request.tg_id)
                reserved = await conn.fetchval(
                    """
                    INSERT INTO promo_redemptions (promo_id, tg_id, discount_amount, was_new_user)
                    VALUES ($1, $2::bigint, $3, $4)
                    ON CONFLICT (promo_id, tg_id) DO UPDATE
                        SET reserved_at = NOW(),
                            discount_amount = EXCLUDED.discount_amount,
                            was_new_user = EXCLUDED.was_new_user
                        WHERE promo_redemptions.status = 'reserved'
                    RETURNING id
                    """,
                    promo_id, request.tg_id, discount_amount, was_new_user,
                )
                return None if reserved is not None else Reason.ALREADY_USED

    async def attach_payment(self, payment_id: str, promo_id: int, tg_id: int, discount_amount: float) -> None:
        """Счёт выставлен со скидкой по коду: пометить платёж и резерв."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE payments SET promo_code_id = $2, promo_discount = $3 WHERE payment_id = $1",
                    payment_id, promo_id, discount_amount,
                )
                await conn.execute(
                    "UPDATE promo_redemptions SET payment_id = $3"
                    " WHERE promo_id = $1 AND tg_id = $2::bigint AND status = 'reserved'",
                    promo_id, tg_id, payment_id,
                )

    async def mark_used(self, payment_id: str, tg_id: int) -> bool:
        """Платёж со скидкой оплачен: активация засчитана. False — платёж был без кода
        (или активация уже засчитана)."""
        used = await self._pool.fetchval(
            """
            UPDATE promo_redemptions r
            SET status = 'used', used_at = NOW(), payment_id = $1
            FROM payments p
            WHERE p.payment_id = $1 AND p.promo_code_id IS NOT NULL
              AND r.promo_id = p.promo_code_id AND r.tg_id = $2::bigint AND r.status = 'reserved'
            RETURNING r.id
            """,
            payment_id, tg_id,
        )
        return used is not None
