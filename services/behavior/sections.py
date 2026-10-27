"""Секции отчёта «Поведение». Каждая отвечает за один блок ответа.

Новый блок = новая секция в ``default_sections()``; сервис и эндпоинт не меняются.
Первые четыре секции считаются по уже накопленным таблицам (ключи, оплаты), поэтому
работают за всю историю; ``Activity`` — по журналу user_events, данные в ней есть
только с момента выката журнала.
"""

from datetime import datetime
from typing import Any, Dict, Protocol

import asyncpg

from services.admin_stats.sections import PAID_AT
from services.behavior.period import BehaviorWindow

# Ключи пользователя: свои и ключи гостя сайта, привязанные к нему (converted_tg_id).
USER_KEYS = "(k.tg_id = {u} OR k.converted_tg_id = {u})"
NOW_MS = "(EXTRACT(EPOCH FROM NOW()) * 1000)"


class BehaviorSection(Protocol):
    name: str

    async def load(self, conn: asyncpg.Connection, window: BehaviorWindow) -> Any: ...


class NewUsersFunnel:
    """Новые Telegram-пользователи окна: сколько взяли ключ, пустили трафик, оплатили.

    «Пошёл трафик» — у ключа сейчас ``used_traffic > 0`` (панель обнуляет счётчик при
    продлении, но продление — уже оплата, она видна на следующем шаге).
    """

    name = "funnel"

    async def load(self, conn: asyncpg.Connection, window: BehaviorWindow) -> Dict[str, int]:
        row = await conn.fetchrow(
            f"""
            WITH cohort AS (
                SELECT u.tg_id FROM users u
                WHERE u.tg_id > 0 AND u.created_at >= $1 AND u.created_at < $2
            ),
            with_keys AS (
                SELECT c.tg_id, BOOL_OR(COALESCE(k.used_traffic, 0) > 0) AS used
                FROM cohort c JOIN keys k ON {USER_KEYS.format(u="c.tg_id")}
                GROUP BY c.tg_id
            )
            SELECT (SELECT COUNT(*) FROM cohort) AS registered,
                   (SELECT COUNT(*) FROM with_keys) AS got_key,
                   (SELECT COUNT(*) FROM with_keys WHERE used) AS connected,
                   (SELECT COUNT(DISTINCT p.tg_id) FROM payments p JOIN cohort c ON c.tg_id = p.tg_id
                    WHERE p.status = 'succeeded') AS paid
            """,
            window.start,
            window.end,
        )
        return dict(row)


class Cohorts:
    """Удержание по месяцу первой оплаты за последние ``months`` месяцев.

    Пользователь «остался» к месяцу N, если самый поздний срок его ключей не раньше
    начала месяца N. Так длинные тарифы (3, 6, 12 месяцев) не считаются уходом.
    Удалённый ключ из расчёта пропадает, такой пользователь считается ушедшим.
    """

    name = "cohorts"

    def __init__(self, months: int = 6):
        self.months = months

    async def load(self, conn: asyncpg.Connection, window: BehaviorWindow) -> list[Dict[str, Any]]:
        now_local = window.end.replace(tzinfo=None)
        this_month = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        index = this_month.year * 12 + this_month.month - 1 - (self.months - 1)
        first_month = datetime(index // 12, index % 12 + 1, 1)
        rows = await conn.fetch(
            f"""
            WITH first_paid AS (
                SELECT p.tg_id, date_trunc('month', MIN({PAID_AT}) AT TIME ZONE $1) AS month
                FROM payments p WHERE p.status = 'succeeded' AND p.tg_id > 0
                GROUP BY p.tg_id
            ),
            cohort AS (
                SELECT f.tg_id, f.month, MAX(k.expiry_time) AS last_expiry
                FROM first_paid f LEFT JOIN keys k ON {USER_KEYS.format(u="f.tg_id")}
                WHERE f.month >= $2
                GROUP BY f.tg_id, f.month
            )
            SELECT c.month, o.k AS offset_months,
                   COUNT(*) FILTER (
                       WHERE c.last_expiry >= EXTRACT(EPOCH FROM ((c.month + make_interval(months => o.k)) AT TIME ZONE $1)) * 1000
                   ) AS retained
            FROM cohort c CROSS JOIN generate_series(1, $3) AS o(k)
            WHERE c.month + make_interval(months => o.k) <= $4
            GROUP BY c.month, o.k
            ORDER BY c.month, o.k
            """,
            window.tz,
            first_month,
            self.months - 1,
            now_local,
        )
        sizes = await _cohort_sizes(conn, window.tz, first_month)
        return _cohort_rows(first_month, self.months, rows, sizes)


async def _cohort_sizes(conn: asyncpg.Connection, tz: str, first_month: datetime) -> Dict[datetime, int]:
    rows = await conn.fetch(
        f"""
        SELECT month, COUNT(*) AS size FROM (
            SELECT date_trunc('month', MIN({PAID_AT}) AT TIME ZONE $1) AS month
            FROM payments p WHERE p.status = 'succeeded' AND p.tg_id > 0 GROUP BY p.tg_id
        ) f WHERE month >= $2 GROUP BY month
        """,
        tz,
        first_month,
    )
    return {r["month"]: r["size"] for r in rows}


def _cohort_rows(first_month: datetime, months: int, rows, sizes: Dict[datetime, int]) -> list[Dict[str, Any]]:
    retained: Dict[datetime, list[int]] = {}
    for r in rows:
        retained.setdefault(r["month"], []).append(r["retained"])
    result = []
    for i in range(months):
        index = first_month.year * 12 + first_month.month - 1 + i
        month = datetime(index // 12, index % 12 + 1, 1)
        result.append(
            {"month": month.strftime("%Y-%m"), "size": sizes.get(month, 0), "retained": retained.get(month, [])}
        )
    return result


class Churn:
    """Отток и продления: кто из платящих ушёл в окне, сколько продлений, кто на грани."""

    name = "churn"

    def __init__(self, risk_days: int = 3, risk_limit: int = 100):
        self.risk_days = risk_days
        self.risk_limit = risk_limit

    async def load(self, conn: asyncpg.Connection, window: BehaviorWindow) -> Dict[str, Any]:
        row = await conn.fetchrow(
            f"""
            WITH paying AS (
                SELECT DISTINCT p.tg_id FROM payments p WHERE p.status = 'succeeded' AND p.tg_id > 0
            ),
            last_expiry AS (
                SELECT p.tg_id, MAX(k.expiry_time) AS last_expiry
                FROM paying p JOIN keys k ON {USER_KEYS.format(u="p.tg_id")}
                GROUP BY p.tg_id
            ),
            free_users AS (
                SELECT k.tg_id, MAX(k.created_at) AS last_created, BOOL_OR(COALESCE(k.used_traffic, 0) > 0) AS used
                FROM keys k
                WHERE k.tg_id > 0 AND NOT EXISTS (
                    SELECT 1 FROM payments p WHERE p.tg_id = k.tg_id AND p.status = 'succeeded'
                )
                GROUP BY k.tg_id
            )
            SELECT
                (SELECT COUNT(*) FROM last_expiry WHERE last_expiry >= $1 AND last_expiry < {NOW_MS}) AS left_users,
                (SELECT COUNT(*) FROM last_expiry WHERE last_expiry >= {NOW_MS}) AS active_paying,
                (SELECT COUNT(*) FROM payments p
                 WHERE p.status = 'succeeded' AND SPLIT_PART(p.payment_type, '|', 1) = 'renew_key'
                   AND {PAID_AT} >= $3 AND {PAID_AT} < $4) AS renewals,
                (SELECT COUNT(*) FROM free_users WHERE NOT used AND last_created >= $1 AND last_created < $2)
                    AS not_connected
            """,
            window.ms(window.start),
            window.ms(window.end),
            window.start,
            window.end,
        )
        at_risk = await conn.fetch(
            f"""
            WITH paying AS (
                SELECT DISTINCT p.tg_id FROM payments p WHERE p.status = 'succeeded' AND p.tg_id > 0
            )
            SELECT k.email, k.tg_id, u.username, u.first_name, k.expiry_time,
                   COALESCE(k.used_traffic, 0) AS used_traffic,
                   COALESCE(t.name_tariff, k.name_tariff) AS tariff
            FROM keys k
            JOIN paying p ON p.tg_id = k.tg_id
            LEFT JOIN users u ON u.tg_id = k.tg_id
            LEFT JOIN tariff t ON t.id = k.tariff_id
            WHERE k.expiry_time >= {NOW_MS} AND k.expiry_time < {NOW_MS} + $1::bigint
              AND NOT EXISTS (
                  SELECT 1 FROM keys other
                  WHERE (other.tg_id = k.tg_id OR other.converted_tg_id = k.tg_id)
                    AND other.expiry_time > k.expiry_time
              )
            ORDER BY k.expiry_time
            LIMIT $2
            """,
            self.risk_days * 86_400_000,
            self.risk_limit,
        )
        result = dict(row)
        result.update(risk_days=self.risk_days, at_risk=[dict(r) for r in at_risk])
        return result


class AbandonedPayments:
    """Счета окна, которые так и не оплатили и после которых пользователь ничего не оплатил."""

    name = "abandoned"

    async def load(self, conn: asyncpg.Connection, window: BehaviorWindow) -> Dict[str, Any]:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS created,
                   COUNT(*) FILTER (WHERE abandoned) AS count,
                   COUNT(DISTINCT tg_id) FILTER (WHERE abandoned) AS users,
                   COALESCE(SUM(amount) FILTER (WHERE abandoned), 0) AS amount
            FROM (
                SELECT p.tg_id, p.amount,
                       p.status <> 'succeeded' AND NOT EXISTS (
                           SELECT 1 FROM payments q
                           WHERE q.tg_id = p.tg_id AND q.status = 'succeeded' AND q.created_at >= p.created_at
                       ) AS abandoned
                FROM payments p
                WHERE p.created_at >= $1 AND p.created_at < $2
            ) w
            """,
            window.start,
            window.end,
        )
        result = dict(row)
        result["amount"] = float(result["amount"])
        return result


class Activity:
    """По журналу событий: сколько людей открывало Mini App и сайт по дням, частота событий."""

    name = "activity"

    async def load(self, conn: asyncpg.Connection, window: BehaviorWindow) -> Dict[str, Any]:
        daily_rows = await conn.fetch(
            """
            SELECT (created_at AT TIME ZONE $3)::date AS day, source, COUNT(DISTINCT tg_id) AS users
            FROM user_events
            WHERE name = 'app_opened' AND tg_id IS NOT NULL AND created_at >= $1 AND created_at < $2
            GROUP BY 1, 2
            """,
            window.start,
            window.end,
            window.tz,
        )
        by_day: Dict[Any, Dict[str, int]] = {}
        for r in daily_rows:
            by_day.setdefault(r["day"], {})[r["source"]] = r["users"]
        daily = [
            {"day": d.isoformat(), "miniapp": by_day.get(d, {}).get("miniapp", 0), "web": by_day.get(d, {}).get("web", 0)}
            for d in window.day_list()
        ]
        totals = await conn.fetchrow(
            """
            SELECT COUNT(DISTINCT tg_id) AS users,
                   COUNT(DISTINCT tg_id) FILTER (WHERE source = 'miniapp') AS miniapp,
                   COUNT(DISTINCT tg_id) FILTER (WHERE source = 'web') AS web
            FROM user_events
            WHERE name = 'app_opened' AND tg_id IS NOT NULL AND created_at >= $1 AND created_at < $2
            """,
            window.start,
            window.end,
        )
        events = await conn.fetch(
            """
            SELECT name, COUNT(*) AS count, COUNT(DISTINCT COALESCE(tg_id::text, anon_id)) AS users
            FROM user_events WHERE created_at >= $1 AND created_at < $2
            GROUP BY name ORDER BY count DESC
            """,
            window.start,
            window.end,
        )
        since = await conn.fetchval("SELECT MIN(created_at) FROM user_events")
        return {
            "since": since,
            "users": totals["users"],
            "miniapp_users": totals["miniapp"],
            "web_users": totals["web"],
            "daily": daily,
            "events": [dict(r) for r in events],
        }


def default_sections() -> list[BehaviorSection]:
    return [NewUsersFunnel(), Cohorts(), Churn(), AbandonedPayments(), Activity()]
