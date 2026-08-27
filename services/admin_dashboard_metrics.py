"""Сервис dashboard-метрик для admin-панели (MRR, воронка, истекающие ключи, платежи).

Портирован из web/app/services/dashboard_metrics.py: раньше web считал эти
метрики прямым SQL к общей Postgres, теперь единственный владелец БД — backend,
и web получает те же данные через GET /api/v1/admin/dashboard-metrics.
"""

from typing import Any, Dict

import asyncpg

from logger import logger


class DashboardMetricsService:
    """Считает сводные dashboard-метрики по одному пулу-подключению."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get_all_dashboard_metrics(self) -> Dict[str, Any]:
        try:
            async with self.pool.acquire() as conn:
                mrr = await self._load_mrr_metrics(conn)
                funnel = await self._load_funnel_metrics(conn)
                expiry = await self._load_key_expiry_metrics(conn)
                payments = await self._load_payment_status_metrics(conn)
        except Exception as e:
            logger.error("admin_dashboard_metrics: ошибка запросов", error=str(e))
            raise

        return {**mrr, **funnel, **expiry, **payments}

    async def _load_mrr_metrics(self, conn: asyncpg.Connection) -> Dict[str, Any]:
        query = """
        WITH monthly_stats AS (
            SELECT
                DATE_TRUNC('month', created_at) as month,
                SUM(amount) as revenue,
                COUNT(DISTINCT tg_id) as paying_users
            FROM payments
            WHERE status = 'succeeded'
            GROUP BY 1
            ORDER BY 1 DESC
            LIMIT 2
        )
        SELECT
            month,
            revenue,
            paying_users,
            revenue / NULLIF(paying_users, 0) as arpu
        FROM monthly_stats
        """
        rows = await conn.fetch(query)

        mrr_current_month = 0.0
        paying_users_current = 0
        arpu_current = 0.0
        mrr_previous_month = 0.0
        mrr_growth = 0.0

        if len(rows) >= 1:
            mrr_current_month = float(rows[0]["revenue"] or 0.0)
            paying_users_current = rows[0]["paying_users"] or 0
            arpu_current = float(rows[0]["arpu"] or 0.0)

        if len(rows) >= 2:
            mrr_previous_month = float(rows[1]["revenue"] or 0.0)
            if mrr_previous_month > 0:
                mrr_growth = (mrr_current_month - mrr_previous_month) / mrr_previous_month * 100

        return {
            "mrr_current_month": mrr_current_month,
            "mrr_previous_month": mrr_previous_month,
            "mrr_growth": mrr_growth,
            "paying_users_current": paying_users_current,
            "arpu_current": arpu_current,
        }

    async def _load_funnel_metrics(self, conn: asyncpg.Connection) -> Dict[str, Any]:
        """users.created_at — TIMESTAMPTZ, keys.created_at — BIGINT (ms), не используется здесь напрямую."""
        query = """
        SELECT
            DATE(u.created_at) as date,
            COUNT(DISTINCT u.tg_id) as new_users,
            COUNT(DISTINCT k.tg_id) as users_with_keys,
            COUNT(DISTINCT p.tg_id) as paying_users
        FROM users u
        LEFT JOIN keys k ON u.tg_id = k.tg_id
        LEFT JOIN payments p ON u.tg_id = p.tg_id AND p.status = 'succeeded'
        WHERE u.created_at >= NOW() - INTERVAL '30 days'
        GROUP BY 1
        ORDER BY 1
        """
        rows = await conn.fetch(query)

        funnel = [
            {
                "date": row["date"].isoformat(),
                "new_users": row["new_users"] or 0,
                "users_with_keys": row["users_with_keys"] or 0,
                "paying_users": row["paying_users"] or 0,
            }
            for row in rows
        ]

        total_new_users_30d = sum(f["new_users"] for f in funnel)
        total_users_with_keys_30d = sum(f["users_with_keys"] for f in funnel)
        total_paying_users_30d = sum(f["paying_users"] for f in funnel)

        conversion_to_keys_pct = 0.0
        conversion_to_paid_pct = 0.0
        if total_new_users_30d > 0:
            conversion_to_keys_pct = total_users_with_keys_30d / total_new_users_30d * 100
            conversion_to_paid_pct = total_paying_users_30d / total_new_users_30d * 100

        return {
            "funnel": funnel,
            "total_new_users_30d": total_new_users_30d,
            "total_users_with_keys_30d": total_users_with_keys_30d,
            "total_paying_users_30d": total_paying_users_30d,
            "conversion_to_keys_pct": conversion_to_keys_pct,
            "conversion_to_paid_pct": conversion_to_paid_pct,
        }

    async def _load_key_expiry_metrics(self, conn: asyncpg.Connection) -> Dict[str, Any]:
        query = """
        SELECT expiry_range, COUNT(*) as keys_count
        FROM (
            SELECT
                CASE
                    WHEN expiry_time <= EXTRACT(EPOCH FROM NOW() + INTERVAL '24 hours') * 1000 THEN 'Менее 24ч'
                    WHEN expiry_time <= EXTRACT(EPOCH FROM NOW() + INTERVAL '48 hours') * 1000 THEN '24-48ч'
                    WHEN expiry_time <= EXTRACT(EPOCH FROM NOW() + INTERVAL '72 hours') * 1000 THEN '48-72ч'
                    ELSE 'Более 72ч'
                END as expiry_range
            FROM keys
            WHERE expiry_time > EXTRACT(EPOCH FROM NOW()) * 1000
        ) subq
        GROUP BY expiry_range
        ORDER BY
            CASE expiry_range
                WHEN 'Менее 24ч' THEN 1
                WHEN '24-48ч' THEN 2
                WHEN '48-72ч' THEN 3
                ELSE 4
            END
        """
        rows = await conn.fetch(query)

        expiring_keys = [
            {"expiry_range": row["expiry_range"], "keys_count": row["keys_count"]}
            for row in rows
        ]
        total_expiring_72h = sum(
            k["keys_count"] for k in expiring_keys
            if k["expiry_range"] in ("Менее 24ч", "24-48ч", "48-72ч")
        )

        return {
            "expiring_keys": expiring_keys,
            "total_expiring_72h": total_expiring_72h,
        }

    async def _load_payment_status_metrics(self, conn: asyncpg.Connection) -> Dict[str, Any]:
        query = """
        SELECT
            status,
            COUNT(*) as count,
            COALESCE(SUM(amount), 0) as total_amount
        FROM payments
        WHERE created_at >= NOW() - INTERVAL '1 year'
        GROUP BY 1
        ORDER BY count DESC
        """
        rows = await conn.fetch(query)

        payment_statuses = [
            {
                "status": row["status"],
                "count": row["count"],
                "total_amount": float(row["total_amount"] or 0.0),
            }
            for row in rows
        ]

        total_succeeded = 0
        total_pending = 0
        total_canceled = 0
        for ps in payment_statuses:
            if ps["status"] == "succeeded":
                total_succeeded = ps["count"]
            elif ps["status"] == "pending":
                total_pending = ps["count"]
            elif ps["status"] == "canceled":
                total_canceled = ps["count"]

        total = total_succeeded + total_pending + total_canceled
        succeeded_pct = (total_succeeded / total * 100) if total > 0 else 0.0

        return {
            "payment_statuses": payment_statuses,
            "total_succeeded": total_succeeded,
            "total_pending": total_pending,
            "total_canceled": total_canceled,
            "succeeded_pct": succeeded_pct,
        }
