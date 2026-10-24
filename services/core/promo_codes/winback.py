"""Акция возврата: личный промокод тем, у кого оплаченный ключ истёк.

Кому: нет активного ключа, последний ключ истёк ``min_days``..``max_days`` дней
назад, была хотя бы одна успешная оплата, предложения не было ``repeat_days`` дней.
Часть людей (``control_percent``) попадает в контрольную группу и получает то же
напоминание без скидки: так видно, сколько возвратов дала именно скидка.
"""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal, Optional

import asyncpg

from models.keys.key import Key
from services.core.promo_codes.models import PromoCode
from services.core.promo_codes.repository import DuplicatePromoCode, PromoCodeRepository

Cohort = Literal["offer", "control"]

CODE_PREFIX = "VERNI-"
# Без похожих символов (0/O, 1/I/L), чтобы код можно было перепечатать руками
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 6
_CREATE_ATTEMPTS = 5


@dataclass(frozen=True)
class WinbackPolicy:
    enabled: bool
    min_days: int
    max_days: int
    discount_percent: int
    valid_hours: int
    max_months: Optional[int]
    repeat_days: int
    control_percent: int
    attribution_days: int

    @classmethod
    def from_settings(cls, settings) -> "WinbackPolicy":
        return cls(
            enabled=settings.winback_enabled,
            min_days=settings.winback_min_days,
            max_days=settings.winback_max_days,
            discount_percent=settings.winback_discount_percent,
            valid_hours=settings.winback_valid_hours,
            max_months=settings.winback_max_months or None,
            repeat_days=settings.winback_repeat_days,
            control_percent=settings.winback_control_percent,
            attribution_days=settings.winback_attribution_days,
        )


def lapsed_key(keys: Iterable[Key], policy: WinbackPolicy, now: datetime) -> Optional[Key]:
    """Последний истёкший ключ, если активных нет и он истёк в окне акции."""
    keys = [k for k in keys if k.expiry_time]
    if not keys:
        return None
    now_ms = int(now.timestamp() * 1000)
    latest = max(keys, key=lambda k: k.expiry_time)
    if latest.expiry_time > now_ms:
        return None
    days_ago = (now_ms - latest.expiry_time) / 86_400_000
    return latest if policy.min_days <= days_ago <= policy.max_days else None


def pick_cohort(tg_id: int, control_percent: int) -> Cohort:
    """Детерминированно: один и тот же человек всегда в одной группе."""
    bucket = int(hashlib.sha256(f"winback:{tg_id}".encode()).hexdigest(), 16) % 100
    return "control" if bucket < control_percent else "offer"


def generate_code() -> str:
    return CODE_PREFIX + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


@dataclass(frozen=True)
class WinbackCohortStats:
    sent: int
    returned: int
    revenue: float


@dataclass(frozen=True)
class WinbackStats:
    days: int
    attribution_days: int
    offer: WinbackCohortStats
    control: WinbackCohortStats
    discount_total: float


class WinbackRepository:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def has_paid(self, tg_id: int) -> bool:
        return bool(await self._pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM payments WHERE tg_id = $1::bigint AND status = 'succeeded')", tg_id
        ))

    async def offered_within(self, tg_id: int, days: int) -> bool:
        return bool(await self._pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM winback_offers"
            " WHERE tg_id = $1::bigint AND sent_at > NOW() - make_interval(days => $2))",
            tg_id, days,
        ))

    async def record(
        self, tg_id: int, cohort: Cohort, promo_id: Optional[int], key_email: str, delivered: bool
    ) -> None:
        await self._pool.execute(
            "INSERT INTO winback_offers (tg_id, cohort, promo_id, key_email, delivered)"
            " VALUES ($1::bigint, $2, $3, $4, $5)",
            tg_id, cohort, promo_id, key_email, delivered,
        )

    async def stats(self, days: int, attribution_days: int) -> WinbackStats:
        """Предложения за ``days`` дней: сколько вернулись (успешная оплата в течение
        ``attribution_days`` после сообщения) в каждой группе."""
        rows = await self._pool.fetch(
            """
            WITH o AS (
                SELECT * FROM winback_offers
                WHERE delivered AND sent_at > NOW() - make_interval(days => $1)
            ),
            back AS (
                SELECT o.id, o.cohort, SUM(p.amount) AS revenue
                FROM o JOIN payments p ON p.tg_id = o.tg_id AND p.status = 'succeeded'
                 AND COALESCE(p.succeeded_at, p.created_at) >= o.sent_at
                 AND COALESCE(p.succeeded_at, p.created_at) < o.sent_at + make_interval(days => $2)
                GROUP BY o.id, o.cohort
            )
            SELECT o.cohort, COUNT(*) AS sent, COUNT(back.id) AS returned,
                   COALESCE(SUM(back.revenue), 0) AS revenue
            FROM o LEFT JOIN back ON back.id = o.id
            GROUP BY o.cohort
            """,
            days, attribution_days,
        )
        by_cohort = {r["cohort"]: WinbackCohortStats(r["sent"], r["returned"], round(float(r["revenue"]), 2)) for r in rows}
        empty = WinbackCohortStats(0, 0, 0.0)
        discount = await self._pool.fetchval(
            """
            SELECT COALESCE(SUM(p.promo_discount), 0) FROM winback_offers o
            JOIN payments p ON p.promo_code_id = o.promo_id AND p.status = 'succeeded'
            WHERE o.cohort = 'offer' AND o.sent_at > NOW() - make_interval(days => $1)
            """,
            days,
        )
        return WinbackStats(
            days=days,
            attribution_days=attribution_days,
            offer=by_cohort.get("offer", empty),
            control=by_cohort.get("control", empty),
            discount_total=round(float(discount), 2),
        )


class WinbackOffers:
    """Решает, кому писать, и выпускает личный код."""

    def __init__(self, policy: WinbackPolicy, offers: WinbackRepository, promo_codes: PromoCodeRepository):
        self.policy = policy
        self._offers = offers
        self._promo_codes = promo_codes

    async def target_key(self, tg_id: int, keys: Iterable[Key], now: datetime) -> Optional[Key]:
        """Ключ, о котором напомнить, или None — этому человеку писать не надо."""
        if not self.policy.enabled:
            return None
        key = lapsed_key(keys, self.policy, now)
        if key is None:
            return None
        if not await self._offers.has_paid(tg_id):
            return None  # только пробный ключ: это не отток, а холодный лид
        if await self._offers.offered_within(tg_id, self.policy.repeat_days):
            return None
        return key

    def cohort(self, tg_id: int) -> Cohort:
        return pick_cohort(tg_id, self.policy.control_percent)

    async def issue_code(self, tg_id: int, now: datetime) -> PromoCode:
        """Личный код: только этому человеку, один раз, на ``valid_hours`` часов."""
        for _ in range(_CREATE_ATTEMPTS):
            try:
                return await self._promo_codes.create(
                    code=generate_code(),
                    discount_percent=self.policy.discount_percent,
                    starts_at=now,
                    ends_at=now + timedelta(hours=self.policy.valid_hours),
                    max_redemptions=1,
                    audience="all",
                    operations="any",
                    max_months=self.policy.max_months,
                    owner_tg_id=tg_id,
                )
            except DuplicatePromoCode:
                continue
        raise RuntimeError("Не удалось выпустить уникальный код акции возврата")

    async def record(self, tg_id: int, cohort: Cohort, promo: Optional[PromoCode], key: Key, delivered: bool) -> None:
        await self._offers.record(tg_id, cohort, promo.id if promo else None, key.email, delivered)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
