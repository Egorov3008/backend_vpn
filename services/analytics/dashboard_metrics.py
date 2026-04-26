"""Сервис Dashboard-метрик для аналитики.

Предоставляет сводные метрики для admin-панели:
- MRR (Monthly Recurring Revenue)
- Воронка пользователей
- Истекающие ключи
- Конверсия платежей
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

import asyncpg

from logger import logger


@dataclass
class MRRMetrics:
    """Метрики ежемесячной выручки."""
    month: datetime
    revenue: float
    paying_users: int
    arpu: float  # Average Revenue Per User


@dataclass
class FunnelMetrics:
    """Метрики воронки за день."""
    date: datetime
    new_users: int
    users_with_keys: int
    paying_users: int


@dataclass
class KeyExpiryMetrics:
    """Метрики истекающих ключей."""
    expiry_range: str  # '<24h', '24-48h', '48-72h'
    keys_count: int


@dataclass
class PaymentStatusMetrics:
    """Метрики платежей по статусам."""
    status: str
    count: int
    total_amount: float


@dataclass
class DashboardMetrics:
    """Сводные метрики dashboard."""
    # MRR
    mrr_current_month: float = 0.0
    mrr_previous_month: float = 0.0
    mrr_growth: float = 0.0  # процент роста
    paying_users_current: int = 0
    arpu_current: float = 0.0

    # Воронка за 30 дней
    funnel: List[FunnelMetrics] = field(default_factory=list)
    total_new_users_30d: int = 0
    total_users_with_keys_30d: int = 0
    total_paying_users_30d: int = 0
    conversion_to_keys_pct: float = 0.0
    conversion_to_paid_pct: float = 0.0

    # Истекающие ключи
    expiring_keys: List[KeyExpiryMetrics] = field(default_factory=list)
    total_expiring_72h: int = 0

    # Платежи
    payment_statuses: List[PaymentStatusMetrics] = field(default_factory=list)
    total_succeeded: int = 0
    total_pending: int = 0
    total_canceled: int = 0
    succeeded_pct: float = 0.0


class DashboardMetricsService:
    """Сервис для получения dashboard-метрик."""

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._db_pool = db_pool

    async def get_all_dashboard_metrics(self) -> DashboardMetrics:
        """Получает все dashboard-метрики."""
        metrics = DashboardMetrics()

        async with self._db_pool.acquire() as conn:
            # MRR метрики
            await self._load_mrr_metrics(conn, metrics)
            # Воронка
            await self._load_funnel_metrics(conn, metrics)
            # Истекающие ключи
            await self._load_key_expiry_metrics(conn, metrics)
            # Статусы платежей
            await self._load_payment_status_metrics(conn, metrics)

        return metrics

    async def _load_mrr_metrics(
        self, conn: asyncpg.Connection, metrics: DashboardMetrics
    ) -> None:
        """Загружает MRR метрики."""
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

        if len(rows) >= 1:
            metrics.mrr_current_month = rows[0]["revenue"] or 0.0
            metrics.paying_users_current = rows[0]["paying_users"] or 0
            metrics.arpu_current = rows[0]["arpu"] or 0.0

        if len(rows) >= 2:
            metrics.mrr_previous_month = rows[1]["revenue"] or 0.0
            # Расчёт процента роста
            if metrics.mrr_previous_month > 0:
                metrics.mrr_growth = (
                    (metrics.mrr_current_month - metrics.mrr_previous_month)
                    / metrics.mrr_previous_month
                    * 100
                )

    async def _load_funnel_metrics(
        self, conn: asyncpg.Connection, metrics: DashboardMetrics
    ) -> None:
        """Загружает метрики воронки за 30 дней.
        
        Примечание: users.created_at имеет тип TIMESTAMPTZ,
        keys.created_at имеет тип BIGINT (milliseconds).
        """
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

        metrics.funnel = [
            FunnelMetrics(
                date=row["date"],
                new_users=row["new_users"] or 0,
                users_with_keys=row["users_with_keys"] or 0,
                paying_users=row["paying_users"] or 0,
            )
            for row in rows
        ]

        # Суммарные показатели за 30 дней
        metrics.total_new_users_30d = sum(f.new_users for f in metrics.funnel)
        metrics.total_users_with_keys_30d = sum(f.users_with_keys for f in metrics.funnel)
        metrics.total_paying_users_30d = sum(f.paying_users for f in metrics.funnel)

        # Конверсии
        if metrics.total_new_users_30d > 0:
            metrics.conversion_to_keys_pct = (
                metrics.total_users_with_keys_30d
                / metrics.total_new_users_30d
                * 100
            )
            metrics.conversion_to_paid_pct = (
                metrics.total_paying_users_30d
                / metrics.total_new_users_30d
                * 100
            )

    async def _load_key_expiry_metrics(
        self, conn: asyncpg.Connection, metrics: DashboardMetrics
    ) -> None:
        """Загружает метрики истекающих ключей."""
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

        metrics.expiring_keys = [
            KeyExpiryMetrics(
                expiry_range=row["expiry_range"],
                keys_count=row["keys_count"],
            )
            for row in rows
        ]

        # Считаем ключи, истекающие в ближайшие 72 часа
        metrics.total_expiring_72h = sum(
            k.keys_count for k in metrics.expiring_keys
            if k.expiry_range in ("Менее 24ч", "24-48ч", "48-72ч")
        )

    async def _load_payment_status_metrics(
        self, conn: asyncpg.Connection, metrics: DashboardMetrics
    ) -> None:
        """Загружает метрики платежей по статусам."""
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

        metrics.payment_statuses = [
            PaymentStatusMetrics(
                status=row["status"],
                count=row["count"],
                total_amount=row["total_amount"] or 0.0,
            )
            for row in rows
        ]

        # Суммарные показатели
        for ps in metrics.payment_statuses:
            if ps.status == "succeeded":
                metrics.total_succeeded = ps.count
            elif ps.status == "pending":
                metrics.total_pending = ps.count
            elif ps.status == "canceled":
                metrics.total_canceled = ps.count

        total = metrics.total_succeeded + metrics.total_pending + metrics.total_canceled
        if total > 0:
            metrics.succeeded_pct = metrics.total_succeeded / total * 100

    async def get_cached(
        self, cache_service, ttl_seconds: int = 300
    ) -> DashboardMetrics:
        """Получает метрики с кэшированием.

        Args:
            cache_service: Сервис кэширования
            ttl_seconds: Время жизни кэша в секундах (по умолчанию 5 минут)

        Returns:
            DashboardMetrics: Объект с метриками
        """
        from datetime import timedelta

        cache_key = "dashboard_metrics"
        cached = await cache_service.storage.get("analytics", cache_key)

        if cached is not None:
            logger.debug("Dashboard metrics loaded from cache")
            return self._dict_to_metrics(cached)

        # Вычисляем из БД
        metrics = await self.get_all_dashboard_metrics()

        # Кэшируем
        await cache_service.storage.set(
            "analytics",
            cache_key,
            self._metrics_to_dict(metrics),
            timedelta(seconds=ttl_seconds),
        )
        logger.debug(f"Dashboard metrics computed and cached for {ttl_seconds}s")

        return metrics

    @staticmethod
    def _metrics_to_dict(metrics: DashboardMetrics) -> dict:
        """Сериализует метрики в dict для кэширования."""
        return {
            "mrr_current_month": metrics.mrr_current_month,
            "mrr_previous_month": metrics.mrr_previous_month,
            "mrr_growth": metrics.mrr_growth,
            "paying_users_current": metrics.paying_users_current,
            "arpu_current": metrics.arpu_current,
            "funnel": [
                {
                    "date": f.date.isoformat(),
                    "new_users": f.new_users,
                    "users_with_keys": f.users_with_keys,
                    "paying_users": f.paying_users,
                }
                for f in metrics.funnel
            ],
            "total_new_users_30d": metrics.total_new_users_30d,
            "total_users_with_keys_30d": metrics.total_users_with_keys_30d,
            "total_paying_users_30d": metrics.total_paying_users_30d,
            "conversion_to_keys_pct": metrics.conversion_to_keys_pct,
            "conversion_to_paid_pct": metrics.conversion_to_paid_pct,
            "expiring_keys": [
                {
                    "expiry_range": k.expiry_range,
                    "keys_count": k.keys_count,
                }
                for k in metrics.expiring_keys
            ],
            "total_expiring_72h": metrics.total_expiring_72h,
            "payment_statuses": [
                {
                    "status": p.status,
                    "count": p.count,
                    "total_amount": p.total_amount,
                }
                for p in metrics.payment_statuses
            ],
            "total_succeeded": metrics.total_succeeded,
            "total_pending": metrics.total_pending,
            "total_canceled": metrics.total_canceled,
            "succeeded_pct": metrics.succeeded_pct,
        }

    @staticmethod
    def _dict_to_metrics(data: dict) -> DashboardMetrics:
        """Десериализует dict обратно в DashboardMetrics."""
        metrics = DashboardMetrics(
            mrr_current_month=data["mrr_current_month"],
            mrr_previous_month=data["mrr_previous_month"],
            mrr_growth=data["mrr_growth"],
            paying_users_current=data["paying_users_current"],
            arpu_current=data["arpu_current"],
            total_new_users_30d=data["total_new_users_30d"],
            total_users_with_keys_30d=data["total_users_with_keys_30d"],
            total_paying_users_30d=data["total_paying_users_30d"],
            conversion_to_keys_pct=data["conversion_to_keys_pct"],
            conversion_to_paid_pct=data["conversion_to_paid_pct"],
            total_expiring_72h=data["total_expiring_72h"],
            total_succeeded=data["total_succeeded"],
            total_pending=data["total_pending"],
            total_canceled=data["total_canceled"],
            succeeded_pct=data["succeeded_pct"],
        )

        # Восстанавливаем списки
        metrics.funnel = [
            FunnelMetrics(
                date=datetime.fromisoformat(f["date"]),
                new_users=f["new_users"],
                users_with_keys=f["users_with_keys"],
                paying_users=f["paying_users"],
            )
            for f in data["funnel"]
        ]

        metrics.expiring_keys = [
            KeyExpiryMetrics(
                expiry_range=k["expiry_range"],
                keys_count=k["keys_count"],
            )
            for k in data["expiring_keys"]
        ]

        metrics.payment_statuses = [
            PaymentStatusMetrics(
                status=p["status"],
                count=p["count"],
                total_amount=p["total_amount"],
            )
            for p in data["payment_statuses"]
        ]

        return metrics
