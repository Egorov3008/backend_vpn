"""Сервис LTV-метрик (Lifetime Value).

Предоставляет метрики ценности пользователей:
- LTV по когортам (1 платеж / 2-3 / 4+ платежей)
- Динамика LTV по месяцам
- Статистика повторных платежей
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

import asyncpg

from logger import logger


@dataclass
class LtvCohort:
    """Когорта пользователей по количеству платежей."""
    cohort_name: str  # '1 платеж', '2-3 платежа', '4+ платежей'
    users_count: int
    avg_ltv: float
    min_ltv: float
    max_ltv: float
    total_revenue: float


@dataclass
class LtvDynamics:
    """Динамика LTV по месяцу."""
    month: datetime
    paying_users: int
    revenue: float
    arpu: float  # Average Revenue Per User


@dataclass
class LtvMetrics:
    """Сводные LTV-метрики."""
    # Когорты
    cohorts: List[LtvCohort] = field(default_factory=list)
    
    # Суммарные показатели
    total_users: int = 0
    total_revenue: float = 0.0
    overall_avg_ltv: float = 0.0
    
    # Динамика по месяцам
    dynamics: List[LtvDynamics] = field(default_factory=list)
    
    # Повторные платежи
    one_time_users: int = 0
    repeat_users: int = 0
    retention_rate: float = 0.0  # процент пользователей с повторными платежами


class LtvMetricsService:
    """Сервис для получения LTV-метрик."""

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._db_pool = db_pool

    async def get_all_ltv_metrics(self) -> LtvMetrics:
        """Получает все LTV-метрики."""
        metrics = LtvMetrics()

        async with self._db_pool.acquire() as conn:
            # Когорты LTV
            await self._load_ltv_by_cohorts(conn, metrics)
            # Динамика LTV
            await self._load_ltv_dynamics(conn, metrics)
            # Повторные платежи
            await self._load_repeat_payment_stats(conn, metrics)

        return metrics

    async def _load_ltv_by_cohorts(
        self, conn: asyncpg.Connection, metrics: LtvMetrics
    ) -> None:
        """Загружает LTV по когортам пользователей."""
        query = """
        SELECT cohort_name, users_count, avg_ltv, min_ltv, max_ltv, total_revenue
        FROM (
            SELECT 
                CASE 
                    WHEN payment_count = 1 THEN '1 платеж'
                    WHEN payment_count <= 3 THEN '2-3 платежа'
                    ELSE '4+ платежей'
                END as cohort_name,
                COUNT(*) as users_count,
                ROUND(AVG(ltv)::numeric, 2)::float as avg_ltv,
                ROUND(MIN(ltv)::numeric, 2)::float as min_ltv,
                ROUND(MAX(ltv)::numeric, 2)::float as max_ltv,
                ROUND(SUM(ltv)::numeric, 2)::float as total_revenue,
                CASE 
                    WHEN payment_count = 1 THEN 1
                    WHEN payment_count <= 3 THEN 2
                    ELSE 3
                END as sort_order
            FROM (
                SELECT 
                    tg_id, 
                    COUNT(*) as payment_count, 
                    SUM(amount) as ltv
                FROM payments 
                WHERE status = 'succeeded'
                GROUP BY tg_id
            ) user_payments
            GROUP BY 1, 7
        ) subq
        ORDER BY sort_order
        """
        rows = await conn.fetch(query)

        metrics.cohorts = [
            LtvCohort(
                cohort_name=row["cohort_name"],
                users_count=row["users_count"],
                avg_ltv=row["avg_ltv"] or 0.0,
                min_ltv=row["min_ltv"] or 0.0,
                max_ltv=row["max_ltv"] or 0.0,
                total_revenue=row["total_revenue"] or 0.0,
            )
            for row in rows
        ]

        # Суммарные показатели
        metrics.total_users = sum(c.users_count for c in metrics.cohorts)
        metrics.total_revenue = sum(c.total_revenue for c in metrics.cohorts)
        if metrics.total_users > 0:
            metrics.overall_avg_ltv = metrics.total_revenue / metrics.total_users

    async def _load_ltv_dynamics(
        self, conn: asyncpg.Connection, metrics: LtvMetrics
    ) -> None:
        """Загружает динамику LTV по месяцам."""
        query = """
        SELECT 
            DATE_TRUNC('month', created_at) as month,
            COUNT(DISTINCT tg_id) as paying_users,
            SUM(amount) as revenue,
            ROUND((SUM(amount) / NULLIF(COUNT(DISTINCT tg_id), 0))::numeric, 2)::float as arpu
        FROM payments 
        WHERE status = 'succeeded'
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 12
        """
        rows = await conn.fetch(query)

        metrics.dynamics = [
            LtvDynamics(
                month=row["month"],
                paying_users=row["paying_users"] or 0,
                revenue=row["revenue"] or 0.0,
                arpu=row["arpu"] or 0.0,
            )
            for row in rows
        ]

    async def _load_repeat_payment_stats(
        self, conn: asyncpg.Connection, metrics: LtvMetrics
    ) -> None:
        """Загружает статистику повторных платежей."""
        query = """
        WITH user_payments AS (
            SELECT tg_id, COUNT(*) as payment_count
            FROM payments 
            WHERE status = 'succeeded'
            GROUP BY tg_id
        )
        SELECT 
            COUNT(*) FILTER (WHERE payment_count = 1) as one_time_users,
            COUNT(*) FILTER (WHERE payment_count > 1) as repeat_users,
            COUNT(*) as total_users
        FROM user_payments
        """
        row = await conn.fetchrow(query)

        metrics.one_time_users = row["one_time_users"] or 0
        metrics.repeat_users = row["repeat_users"] or 0
        
        total = row["total_users"] or 0
        if total > 0:
            metrics.retention_rate = (metrics.repeat_users / total) * 100

    async def get_cached(
        self, cache_service, ttl_seconds: int = 300
    ) -> LtvMetrics:
        """Получает метрики с кэшированием.

        Args:
            cache_service: Сервис кэширования
            ttl_seconds: Время жизни кэша в секундах (по умолчанию 5 минут)

        Returns:
            LtvMetrics: Объект с метриками
        """
        from datetime import timedelta

        cache_key = "ltv_metrics"
        cached = await cache_service.storage.get("analytics", cache_key)

        if cached is not None:
            logger.debug("LTV metrics loaded from cache")
            return self._dict_to_metrics(cached)

        # Вычисляем из БД
        metrics = await self.get_all_ltv_metrics()

        # Кэшируем
        await cache_service.storage.set(
            "analytics",
            cache_key,
            self._metrics_to_dict(metrics),
            timedelta(seconds=ttl_seconds),
        )
        logger.debug(f"LTV metrics computed and cached for {ttl_seconds}s")

        return metrics

    @staticmethod
    def _metrics_to_dict(metrics: LtvMetrics) -> dict:
        """Сериализует метрики в dict для кэширования."""
        return {
            "cohorts": [
                {
                    "cohort_name": c.cohort_name,
                    "users_count": c.users_count,
                    "avg_ltv": c.avg_ltv,
                    "min_ltv": c.min_ltv,
                    "max_ltv": c.max_ltv,
                    "total_revenue": c.total_revenue,
                }
                for c in metrics.cohorts
            ],
            "total_users": metrics.total_users,
            "total_revenue": metrics.total_revenue,
            "overall_avg_ltv": metrics.overall_avg_ltv,
            "dynamics": [
                {
                    "month": d.month.isoformat(),
                    "paying_users": d.paying_users,
                    "revenue": d.revenue,
                    "arpu": d.arpu,
                }
                for d in metrics.dynamics
            ],
            "one_time_users": metrics.one_time_users,
            "repeat_users": metrics.repeat_users,
            "retention_rate": metrics.retention_rate,
        }

    @staticmethod
    def _dict_to_metrics(data: dict) -> LtvMetrics:
        """Десериализует dict обратно в LtvMetrics."""
        metrics = LtvMetrics(
            total_users=data["total_users"],
            total_revenue=data["total_revenue"],
            overall_avg_ltv=data["overall_avg_ltv"],
            one_time_users=data["one_time_users"],
            repeat_users=data["repeat_users"],
            retention_rate=data["retention_rate"],
        )

        # Восстанавливаем списки
        metrics.cohorts = [
            LtvCohort(
                cohort_name=c["cohort_name"],
                users_count=c["users_count"],
                avg_ltv=c["avg_ltv"],
                min_ltv=c["min_ltv"],
                max_ltv=c["max_ltv"],
                total_revenue=c["total_revenue"],
            )
            for c in data["cohorts"]
        ]

        metrics.dynamics = [
            LtvDynamics(
                month=datetime.fromisoformat(d["month"]),
                paying_users=d["paying_users"],
                revenue=d["revenue"],
                arpu=d["arpu"],
            )
            for d in data["dynamics"]
        ]

        return metrics
