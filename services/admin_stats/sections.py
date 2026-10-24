"""Секции статистики. Каждая отвечает за один блок ответа.

Новый блок = новая секция в ``default_sections()``; сервис и эндпоинт не меняются.
"""

from datetime import datetime
from typing import Any, Dict, Protocol

import asyncpg

from services.admin_stats.period import MonthPeriod

# Момент оплаты: succeeded_at (с миграции 20261013), у исторических строк — created_at.
PAID_AT = "COALESCE(p.succeeded_at, p.created_at)"
# keys.created_at и expiry_time хранятся в миллисекундах (BIGINT).
KEY_CREATED_AT = "to_timestamp(k.created_at / 1000.0)"
KEY_ACTIVE = "k.expiry_time > EXTRACT(EPOCH FROM NOW()) * 1000"
# Платный ключ: выдан по платежу или на тарифе с ценой. Пробные, лендинг, подарки — бесплатные.
KEY_IS_PAID = "(k.payment_id IS NOT NULL OR COALESCE(t.amount, k.amount, 0) > 0)"
# Реальный Telegram-пользователь. Отрицательный tg_id — анонимный гость сайта (landing._pseudo_tg_id).
REAL_USER = "u.tg_id > 0"


class StatsSection(Protocol):
    """Блок ответа: ``name`` — ключ в JSON, ``load`` — данные за период."""

    name: str

    async def load(self, conn: asyncpg.Connection, period: MonthPeriod) -> Any: ...


async def count_by_day(
    conn: asyncpg.Connection, period: MonthPeriod, *, ts: str, source: str, value: str = "COUNT(*)"
) -> tuple[list[float], list[float]]:
    """``value`` по дням текущего и прошлого месяца: (current[day-1], previous[day-1]).

    ``source`` — FROM/WHERE без условия на время, ``ts`` — выражение момента события.
    Дни текущего месяца после ``cutoff`` в ответ не попадают (длина = days_elapsed).
    """
    query = f"""
    SELECT (({ts}) AT TIME ZONE $3)::date AS day, {value} AS v
    {source} AND ({ts}) >= $1 AND ({ts}) < $2
    GROUP BY 1
    """

    async def series(start: datetime, end: datetime, length: int) -> list[float]:
        values = [0.0] * length
        for row in await conn.fetch(query, start, end, period.tz):
            index = row["day"].day - 1
            if 0 <= index < length:
                values[index] = float(row["v"] or 0)
        return values

    current = await series(period.start, period.cutoff, period.days_elapsed)
    previous = await series(period.prev_start, period.start, period.prev_days_in_month)
    return current, previous


def _compare(current: Dict[str, Any], previous_same_days: Dict[str, Any], previous_full: Dict[str, Any]) -> Dict[str, Any]:
    return {"current": current, "previous_same_days": previous_same_days, "previous_full": previous_full}


class Payments:
    """Успешные оплаты месяца против того же отрезка и всего прошлого месяца, плюс ряды по дням."""

    name = "payments"
    _SOURCE = "FROM payments p WHERE p.status = 'succeeded'"

    async def load(self, conn: asyncpg.Connection, period: MonthPeriod) -> Dict[str, Any]:
        daily, daily_prev = await count_by_day(conn, period, ts=PAID_AT, source=self._SOURCE, value="SUM(p.amount)")
        result = _compare(
            await self._window(conn, period.start, period.cutoff),
            await self._window(conn, period.prev_start, period.prev_cutoff),
            await self._window(conn, period.prev_start, period.start),
        )
        result.update(daily_revenue=daily, daily_revenue_prev=daily_prev)
        return result

    async def _window(self, conn: asyncpg.Connection, start: datetime, end: datetime) -> Dict[str, Any]:
        row = await conn.fetchrow(
            f"""
            SELECT COALESCE(SUM(p.amount), 0) AS revenue, COUNT(*) AS count,
                   COUNT(DISTINCT p.tg_id) AS paying_users
            {self._SOURCE} AND {PAID_AT} >= $1 AND {PAID_AT} < $2
            """,
            start,
            end,
        )
        return {"revenue": float(row["revenue"]), "count": row["count"], "paying_users": row["paying_users"]}


class Activity:
    """Сейчас: пользователи с действующим ключом и действующие ключи (не зависит от месяца)."""

    name = "activity"

    async def load(self, conn: asyncpg.Connection, period: MonthPeriod) -> Dict[str, int]:
        row = await conn.fetchrow(
            f"""
            SELECT COUNT(DISTINCT k.tg_id) FILTER (WHERE k.tg_id > 0) AS active_users,
                   COUNT(*) AS active_keys,
                   COUNT(*) FILTER (WHERE {KEY_IS_PAID}) AS active_keys_paid,
                   COUNT(*) FILTER (WHERE k.landing_uid IS NOT NULL AND k.tg_id < 0) AS active_keys_guest
            FROM keys k
            LEFT JOIN tariff t ON t.id = k.tariff_id
            WHERE {KEY_ACTIVE}
            """
        )
        return dict(row)


class NewUsers:
    """Новые Telegram-пользователи месяца и их оплаты в этом же месяце."""

    name = "new_users"
    _SOURCE = f"FROM users u WHERE {REAL_USER}"

    async def load(self, conn: asyncpg.Connection, period: MonthPeriod) -> Dict[str, Any]:
        daily, daily_prev = await count_by_day(conn, period, ts="u.created_at", source=self._SOURCE)
        result = _compare(
            await self._cohort(conn, period.start, period.cutoff),
            await self._cohort(conn, period.prev_start, period.prev_cutoff),
            await self._cohort(conn, period.prev_start, period.start),
        )
        result.update(daily=daily, daily_prev=daily_prev)
        return result

    @staticmethod
    async def _cohort(conn: asyncpg.Connection, start: datetime, end: datetime) -> Dict[str, Any]:
        row = await conn.fetchrow(
            f"""
            WITH cohort AS (
                SELECT u.tg_id FROM users u
                WHERE {REAL_USER} AND u.created_at >= $1 AND u.created_at < $2
            ),
            paid AS (
                SELECT p.tg_id, p.amount FROM payments p JOIN cohort c ON c.tg_id = p.tg_id
                WHERE p.status = 'succeeded' AND {PAID_AT} >= $1 AND {PAID_AT} < $2
            )
            SELECT (SELECT COUNT(*) FROM cohort) AS count,
                   (SELECT COUNT(DISTINCT tg_id) FROM paid) AS paid_users,
                   (SELECT COUNT(*) FROM paid) AS payments,
                   (SELECT COALESCE(SUM(amount), 0) FROM paid) AS revenue
            """,
            start,
            end,
        )
        return {
            "count": row["count"],
            "paid_users": row["paid_users"],
            "payments": row["payments"],
            "revenue": float(row["revenue"]),
        }


class WebSignups:
    """Регистрации с сайта: 24-часовой ключ гостя (ключ с landing_uid) и что с ним стало.

    Гость, привязавший Telegram, — ключ с ``converted_tg_id``; оплатил — у этого
    пользователя есть успешная оплата после выдачи ключа.
    """

    name = "web"
    _SOURCE = "FROM keys k WHERE k.landing_uid IS NOT NULL"

    async def load(self, conn: asyncpg.Connection, period: MonthPeriod) -> Dict[str, Any]:
        daily, daily_prev = await count_by_day(conn, period, ts=KEY_CREATED_AT, source=self._SOURCE)
        result = _compare(
            await self._window(conn, period.start, period.cutoff),
            await self._window(conn, period.prev_start, period.prev_cutoff),
            await self._window(conn, period.prev_start, period.start),
        )
        result.update(daily=daily, daily_prev=daily_prev)
        return result

    async def _window(self, conn: asyncpg.Connection, start: datetime, end: datetime) -> Dict[str, int]:
        row = await conn.fetchrow(
            f"""
            SELECT COUNT(*) AS guests,
                   COUNT(*) FILTER (WHERE k.converted_tg_id IS NOT NULL) AS linked_telegram,
                   COUNT(*) FILTER (WHERE EXISTS (
                       SELECT 1 FROM payments p
                       WHERE p.tg_id = k.converted_tg_id AND p.status = 'succeeded'
                         AND {PAID_AT} >= {KEY_CREATED_AT}
                   )) AS paid
            {self._SOURCE} AND {KEY_CREATED_AT} >= $1 AND {KEY_CREATED_AT} < $2
            """,
            start,
            end,
        )
        return dict(row)


class TopTariffs:
    """Тарифы по числу успешных оплат месяца (покупки и продления).

    Тариф покупки — из ``payment_type`` (``create_key|<tariff_id>``), продления
    (``renew_key|<email>``) — текущий тариф продлённого ключа.
    """

    name = "tariffs"

    async def load(self, conn: asyncpg.Connection, period: MonthPeriod) -> list[Dict[str, Any]]:
        rows = await conn.fetch(
            f"""
            WITH paid AS (
                SELECT p.amount,
                       CASE
                           WHEN SPLIT_PART(p.payment_type, '|', 1) = 'create_key'
                                AND SPLIT_PART(p.payment_type, '|', 2) ~ '^[0-9]+$'
                               THEN SPLIT_PART(p.payment_type, '|', 2)::int
                           WHEN SPLIT_PART(p.payment_type, '|', 1) = 'renew_key'
                               THEN (SELECT k.tariff_id FROM keys k
                                     WHERE k.email = SPLIT_PART(p.payment_type, '|', 2) LIMIT 1)
                       END AS tariff_id
                FROM payments p
                WHERE p.status = 'succeeded' AND {PAID_AT} >= $1 AND {PAID_AT} < $2
            )
            SELECT paid.tariff_id, COALESCE(t.name_tariff, 'Тариф не найден') AS tariff,
                   COUNT(*) AS payments, COALESCE(SUM(paid.amount), 0) AS revenue
            FROM paid LEFT JOIN tariff t ON t.id = paid.tariff_id
            GROUP BY paid.tariff_id, t.name_tariff
            ORDER BY payments DESC, revenue DESC, tariff
            """,
            period.start,
            period.cutoff,
        )
        return [
            {"tariff_id": r["tariff_id"], "tariff": r["tariff"], "payments": r["payments"], "revenue": float(r["revenue"])}
            for r in rows
        ]
