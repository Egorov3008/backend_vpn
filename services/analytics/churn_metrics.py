"""Сервис метрик оттока (Churn Rate).

Предоставляет метрики оттока и удержания пользователей:
- Churn rate за период (30/60/90 дней)
- Отток по когортам (по месяцу регистрации)
- Retention rate (удержание)
- Динамика активных пользователей
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import asyncpg

from logger import logger


@dataclass
class ChurnCohort:
    """Когорта оттока по месяцу регистрации."""
    cohort_month: datetime
    total_users: int
    retained_users: int
    churned_users: int
    churn_rate: float  # процент ушедших
    retention_rate: float  # процент оставшихся


@dataclass
class ChurnPeriodMetrics:
    """Метрики оттока за период."""
    period_days: int
    total_users: int
    active_users: int
    churned_users: int
    churn_rate: float  # процент ушедших
    retention_rate: float  # процент оставшихся


@dataclass
class ActiveUsersTrend:
    """Тренд активных пользователей."""
    date: datetime
    active_users: int
    new_users: int
    churned_users: int


@dataclass
class ChurnMetrics:
    """Сводные метрики оттока."""
    # Отток за периоды
    churn_30d: Optional[ChurnPeriodMetrics] = None
    churn_60d: Optional[ChurnPeriodMetrics] = None
    churn_90d: Optional[ChurnPeriodMetrics] = None
    
    # Отток по когортам
    cohorts: List[ChurnCohort] = field(default_factory=list)
    
    # Тренд активных пользователей
    active_trend: List[ActiveUsersTrend] = field(default_factory=list)
    
    # Общие показатели
    overall_churn_rate: float = 0.0
    overall_retention_rate: float = 0.0
    total_users: int = 0
    total_active: int = 0


class ChurnMetricsService:
    """Сервис для получения метрик оттока."""

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._db_pool = db_pool

    async def get_all_churn_metrics(self) -> ChurnMetrics:
        """Получает все метрики оттока."""
        metrics = ChurnMetrics()

        async with self._db_pool.acquire() as conn:
            # Churn rate за периоды
            await self._load_churn_by_period(conn, metrics, 30)
            await self._load_churn_by_period(conn, metrics, 60)
            await self._load_churn_by_period(conn, metrics, 90)
            
            # Отток по когортам
            await self._load_churn_by_cohorts(conn, metrics)
            
            # Тренд активных пользователей
            await self._load_active_users_trend(conn, metrics)
            
            # Общие показатели
            await self._load_overall_metrics(conn, metrics)

        return metrics

    async def _load_churn_by_period(
        self, conn: asyncpg.Connection, metrics: ChurnMetrics, period_days: int
    ) -> None:
        """Загружает метрики оттока за указанный период."""
        query = """
        WITH period_start AS (
            SELECT NOW() - INTERVAL '%(period_days)s days' as start_date
        ),
        all_users_in_period AS (
            SELECT tg_id, created_at
            FROM users
            WHERE created_at < (SELECT start_date FROM period_start)
        ),
        active_users AS (
            SELECT DISTINCT u.tg_id
            FROM all_users_in_period u
            INNER JOIN keys k ON k.tg_id = u.tg_id
            WHERE k.expiry_time > EXTRACT(EPOCH FROM NOW()) * 1000
        ),
        churned AS (
            SELECT u.tg_id
            FROM all_users_in_period u
            WHERE u.tg_id NOT IN (SELECT tg_id FROM active_users)
        )
        SELECT 
            COUNT(*) as total_users,
            (SELECT COUNT(*) FROM active_users) as active_users,
            (SELECT COUNT(*) FROM churned) as churned_users,
            ROUND(
                (SELECT COUNT(*) FROM churned)::numeric / 
                NULLIF(COUNT(*), 0) * 100, 
                2
            ) as churn_rate
        FROM all_users_in_period
        """ % {"period_days": period_days}
        
        row = await conn.fetchrow(query)

        churn_metrics = ChurnPeriodMetrics(
            period_days=period_days,
            total_users=row["total_users"] or 0,
            active_users=row["active_users"] or 0,
            churned_users=row["churned_users"] or 0,
            churn_rate=float(row["churn_rate"] or 0.0),
            retention_rate=100.0 - float(row["churn_rate"] or 0.0),
        )

        if period_days == 30:
            metrics.churn_30d = churn_metrics
        elif period_days == 60:
            metrics.churn_60d = churn_metrics
        elif period_days == 90:
            metrics.churn_90d = churn_metrics

    async def _load_churn_by_cohorts(
        self, conn: asyncpg.Connection, metrics: ChurnMetrics
    ) -> None:
        """Загружает отток по когортам (месяцам регистрации)."""
        query = """
        SELECT 
            cohort_month,
            total_users,
            retained_users,
            churned_users,
            churn_rate,
            retention_rate
        FROM (
            SELECT 
                DATE_TRUNC('month', u.created_at) as cohort_month,
                COUNT(*) as total_users,
                COUNT(DISTINCT k.tg_id) as retained_users,
                COUNT(*) - COUNT(DISTINCT k.tg_id) as churned_users,
                ROUND(
                    (1 - COUNT(DISTINCT k.tg_id)::numeric / NULLIF(COUNT(*), 0)) * 100, 
                    2
                ) as churn_rate,
                ROUND(
                    COUNT(DISTINCT k.tg_id)::numeric / NULLIF(COUNT(*), 0) * 100, 
                    2
                ) as retention_rate
            FROM users u
            LEFT JOIN keys k ON u.tg_id = k.tg_id 
                AND k.expiry_time > EXTRACT(EPOCH FROM NOW()) * 1000
            GROUP BY 1
        ) subq
        ORDER BY cohort_month DESC
        LIMIT 12
        """
        rows = await conn.fetch(query)

        metrics.cohorts = [
            ChurnCohort(
                cohort_month=row["cohort_month"],
                total_users=row["total_users"],
                retained_users=row["retained_users"],
                churned_users=row["churned_users"],
                churn_rate=row["churn_rate"] or 0.0,
                retention_rate=row["retention_rate"] or 0.0,
            )
            for row in rows
        ]

    async def _load_active_users_trend(
        self, conn: asyncpg.Connection, metrics: ChurnMetrics
    ) -> None:
        """Загружает тренд активных пользователей по дням."""
        query = """
        WITH date_range AS (
            SELECT generate_series(
                NOW() - INTERVAL '30 days',
                NOW(),
                INTERVAL '1 day'
            )::date as date
        ),
        active_by_day AS (
            SELECT 
                d.date,
                COUNT(DISTINCT k.tg_id) as active_users
            FROM date_range d
            LEFT JOIN keys k ON k.tg_id IS NOT NULL
                AND k.expiry_time > EXTRACT(EPOCH FROM d.date) * 1000
                AND k.expiry_time <= EXTRACT(EPOCH FROM d.date + INTERVAL '1 day') * 1000
            GROUP BY d.date
        ),
        new_by_day AS (
            SELECT 
                d.date,
                COUNT(u.tg_id) as new_users
            FROM date_range d
            LEFT JOIN users u ON DATE(u.created_at) = d.date
            GROUP BY d.date
        )
        SELECT 
            d.date,
            COALESCE(a.active_users, 0) as active_users,
            COALESCE(n.new_users, 0) as new_users,
            0 as churned_users
        FROM date_range d
        LEFT JOIN active_by_day a ON d.date = a.date
        LEFT JOIN new_by_day n ON d.date = n.date
        ORDER BY d.date DESC
        """
        rows = await conn.fetch(query)

        metrics.active_trend = [
            ActiveUsersTrend(
                date=row["date"],
                active_users=row["active_users"] or 0,
                new_users=row["new_users"] or 0,
                churned_users=row["churned_users"] or 0,
            )
            for row in rows
        ]

    async def _load_overall_metrics(
        self, conn: asyncpg.Connection, metrics: ChurnMetrics
    ) -> None:
        """Загружает общие метрики оттока."""
        query = """
        SELECT 
            (SELECT COUNT(*) FROM users) as total_users,
            COUNT(DISTINCT k.tg_id) as total_active
        FROM keys k
        WHERE k.expiry_time > EXTRACT(EPOCH FROM NOW()) * 1000
        """
        row = await conn.fetchrow(query)

        metrics.total_users = row["total_users"] or 0
        metrics.total_active = row["total_active"] or 0
        
        if metrics.total_users > 0:
            metrics.overall_retention_rate = (
                metrics.total_active / metrics.total_users
            ) * 100
            metrics.overall_churn_rate = (
                100.0 - metrics.overall_retention_rate
            )

    async def get_cached(
        self, cache_service, ttl_seconds: int = 300
    ) -> ChurnMetrics:
        """Получает метрики с кэшированием.

        Args:
            cache_service: Сервис кэширования
            ttl_seconds: Время жизни кэша в секундах (по умолчанию 5 минут)

        Returns:
            ChurnMetrics: Объект с метриками
        """
        from datetime import timedelta

        cache_key = "churn_metrics"
        cached = await cache_service.storage.get("analytics", cache_key)

        if cached is not None:
            logger.debug("Churn metrics loaded from cache")
            return self._dict_to_metrics(cached)

        # Вычисляем из БД
        metrics = await self.get_all_churn_metrics()

        # Кэшируем
        await cache_service.storage.set(
            "analytics",
            cache_key,
            self._metrics_to_dict(metrics),
            timedelta(seconds=ttl_seconds),
        )
        logger.debug(f"Churn metrics computed and cached for {ttl_seconds}s")

        return metrics

    @staticmethod
    def _metrics_to_dict(metrics: ChurnMetrics) -> dict:
        """Сериализует метрики в dict для кэширования."""
        return {
            "churn_30d": (
                {
                    "period_days": metrics.churn_30d.period_days,
                    "total_users": metrics.churn_30d.total_users,
                    "active_users": metrics.churn_30d.active_users,
                    "churned_users": metrics.churn_30d.churned_users,
                    "churn_rate": metrics.churn_30d.churn_rate,
                    "retention_rate": metrics.churn_30d.retention_rate,
                }
                if metrics.churn_30d
                else None
            ),
            "churn_60d": (
                {
                    "period_days": metrics.churn_60d.period_days,
                    "total_users": metrics.churn_60d.total_users,
                    "active_users": metrics.churn_60d.active_users,
                    "churned_users": metrics.churn_60d.churned_users,
                    "churn_rate": metrics.churn_60d.churn_rate,
                    "retention_rate": metrics.churn_60d.retention_rate,
                }
                if metrics.churn_60d
                else None
            ),
            "churn_90d": (
                {
                    "period_days": metrics.churn_90d.period_days,
                    "total_users": metrics.churn_90d.total_users,
                    "active_users": metrics.churn_90d.active_users,
                    "churned_users": metrics.churn_90d.churned_users,
                    "churn_rate": metrics.churn_90d.churn_rate,
                    "retention_rate": metrics.churn_90d.retention_rate,
                }
                if metrics.churn_90d
                else None
            ),
            "cohorts": [
                {
                    "cohort_month": c.cohort_month.isoformat(),
                    "total_users": c.total_users,
                    "retained_users": c.retained_users,
                    "churned_users": c.churned_users,
                    "churn_rate": c.churn_rate,
                    "retention_rate": c.retention_rate,
                }
                for c in metrics.cohorts
            ],
            "active_trend": [
                {
                    "date": t.date.isoformat(),
                    "active_users": t.active_users,
                    "new_users": t.new_users,
                    "churned_users": t.churned_users,
                }
                for t in metrics.active_trend
            ],
            "overall_churn_rate": metrics.overall_churn_rate,
            "overall_retention_rate": metrics.overall_retention_rate,
            "total_users": metrics.total_users,
            "total_active": metrics.total_active,
        }

    @staticmethod
    def _dict_to_metrics(data: dict) -> ChurnMetrics:
        """Десериализует dict обратно в ChurnMetrics."""
        metrics = ChurnMetrics(
            overall_churn_rate=data["overall_churn_rate"],
            overall_retention_rate=data["overall_retention_rate"],
            total_users=data["total_users"],
            total_active=data["total_active"],
        )

        # Восстанавливаем метрики по периодам
        if data.get("churn_30d"):
            d = data["churn_30d"]
            metrics.churn_30d = ChurnPeriodMetrics(
                period_days=d["period_days"],
                total_users=d["total_users"],
                active_users=d["active_users"],
                churned_users=d["churned_users"],
                churn_rate=d["churn_rate"],
                retention_rate=d["retention_rate"],
            )
        
        if data.get("churn_60d"):
            d = data["churn_60d"]
            metrics.churn_60d = ChurnPeriodMetrics(
                period_days=d["period_days"],
                total_users=d["total_users"],
                active_users=d["active_users"],
                churned_users=d["churned_users"],
                churn_rate=d["churn_rate"],
                retention_rate=d["retention_rate"],
            )
        
        if data.get("churn_90d"):
            d = data["churn_90d"]
            metrics.churn_90d = ChurnPeriodMetrics(
                period_days=d["period_days"],
                total_users=d["total_users"],
                active_users=d["active_users"],
                churned_users=d["churned_users"],
                churn_rate=d["churn_rate"],
                retention_rate=d["retention_rate"],
            )

        # Восстанавливаем когорты
        metrics.cohorts = [
            ChurnCohort(
                cohort_month=datetime.fromisoformat(c["cohort_month"]),
                total_users=c["total_users"],
                retained_users=c["retained_users"],
                churned_users=c["churned_users"],
                churn_rate=c["churn_rate"],
                retention_rate=c["retention_rate"],
            )
            for c in data["cohorts"]
        ]

        # Восстанавливаем тренд
        metrics.active_trend = [
            ActiveUsersTrend(
                date=datetime.fromisoformat(t["date"]),
                active_users=t["active_users"],
                new_users=t["new_users"],
                churned_users=t["churned_users"],
            )
            for t in data["active_trend"]
        ]

        return metrics
