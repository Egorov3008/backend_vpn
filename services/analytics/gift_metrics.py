"""Сервис метрик подарков.

Предоставляет метрики эффективности системы подарков:
- Общая статистика подарков
- Статус активации
- Среднее время до активации
- Популярные тарифы для подарков
"""

from dataclasses import dataclass, field
from typing import List, Optional

import asyncpg

from logger import logger


@dataclass
class GiftOverview:
    """Общая статистика подарков."""
    total_gifts: int  # Всего подарков
    total_senders: int  # Уникальных отправителей
    total_recipients: int  # Уникальных получателей
    activated_count: int  # Активировано
    not_activated_count: int  # Не активировано
    activation_rate: float  # Процент активации


@dataclass
class GiftActivationStats:
    """Статистика активации подарков."""
    avg_activation_hours: float  # Среднее время до активации (часы)
    median_activation_hours: float  # Медианное время (часы)
    activated_within_24h: int  # Активировано в первые 24 часа
    activated_within_week: int  # Активировано в первую неделю


@dataclass
class PopularGiftTariff:
    """Популярный тариф для подарков."""
    tariff_name: str
    gifts_count: int
    activated_count: int
    activation_rate: float


@dataclass
class GiftMetrics:
    """Сводные метрики подарков."""
    # Общая статистика
    overview: Optional[GiftOverview] = None
    
    # Статистика активации
    activation_stats: Optional[GiftActivationStats] = None
    
    # Популярные тарифы
    popular_tariffs: List[PopularGiftTariff] = field(default_factory=list)
    
    # Динамика по месяцам
    monthly_gifts: List[dict] = field(default_factory=list)


class GiftMetricsService:
    """Сервис для получения метрик подарков."""

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._db_pool = db_pool

    async def get_all_gift_metrics(self) -> GiftMetrics:
        """Получает все метрики подарков.

        Returns:
            GiftMetrics: Объект с метриками
        """
        metrics = GiftMetrics()

        async with self._db_pool.acquire() as conn:
            # Общая статистика
            await self._load_overview(conn, metrics)
            
            # Статистика активации
            await self._load_activation_stats(conn, metrics)
            
            # Популярные тарифы
            await self._load_popular_tariffs(conn, metrics)
            
            # Динамика по месяцам
            await self._load_monthly_gifts(conn, metrics)

        return metrics

    async def _load_overview(
        self, conn: asyncpg.Connection, metrics: GiftMetrics
    ) -> None:
        """Загружает общую статистику подарков."""
        query = """
        SELECT
            COUNT(*) as total_gifts,
            COUNT(DISTINCT sender_tg_id) as total_senders,
            COUNT(DISTINCT recipient_tg_id) as total_recipients,
            COUNT(*) FILTER (WHERE used_at IS NOT NULL) as activated_count,
            COUNT(*) FILTER (WHERE used_at IS NULL) as not_activated_count
        FROM gift_links
        """
        row = await conn.fetchrow(query)

        total_gifts = row["total_gifts"] or 0
        activated_count = row["activated_count"] or 0

        metrics.overview = GiftOverview(
            total_gifts=total_gifts,
            total_senders=row["total_senders"] or 0,
            total_recipients=row["total_recipients"] or 0,
            activated_count=activated_count,
            not_activated_count=row["not_activated_count"] or 0,
            activation_rate=(
                (activated_count / total_gifts * 100) if total_gifts > 0 else 0.0
            ),
        )

    async def _load_activation_stats(
        self, conn: asyncpg.Connection, metrics: GiftMetrics
    ) -> None:
        """Загружает статистику активации подарков."""
        query = """
        WITH activation_times AS (
            SELECT
                EXTRACT(EPOCH FROM (used_at - created_at)) / 3600 as activation_hours
            FROM gift_links
            WHERE used_at IS NOT NULL
        ),
        percentiles AS (
            SELECT 
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY activation_hours) as median_hours
            FROM activation_times
        )
        SELECT 
            AVG(activation_hours) as avg_hours,
            p.median_hours,
            COUNT(*) FILTER (WHERE activation_hours <= 24) as within_24h,
            COUNT(*) FILTER (WHERE activation_hours <= 168) as within_week,
            COUNT(*) as total_activated
        FROM activation_times
        CROSS JOIN percentiles p
        """
        row = await conn.fetchrow(query)

        if row and row["total_activated"] and row["total_activated"] > 0:
            metrics.activation_stats = GiftActivationStats(
                avg_activation_hours=round(row["avg_hours"] or 0.0, 2),
                median_activation_hours=round(row["median_hours"] or 0.0, 2),
                activated_within_24h=row["within_24h"] or 0,
                activated_within_week=row["within_week"] or 0,
            )
        else:
            metrics.activation_stats = GiftActivationStats(
                avg_activation_hours=0.0,
                median_activation_hours=0.0,
                activated_within_24h=0,
                activated_within_week=0,
            )

    async def _load_popular_tariffs(
        self, conn: asyncpg.Connection, metrics: GiftMetrics
    ) -> None:
        """Загружает популярные тарифы для подарков."""
        query = """
        SELECT
            t.name_tariff as tariff_name,
            COUNT(*) as gifts_count,
            COUNT(*) FILTER (WHERE g.used_at IS NOT NULL) as activated_count,
            ROUND(
                COUNT(*) FILTER (WHERE g.used_at IS NOT NULL)::numeric /
                NULLIF(COUNT(*), 0) * 100,
                2
            ) as activation_rate
        FROM gift_links g
        INNER JOIN tariff t ON g.tariff_id = t.id
        GROUP BY t.name_tariff
        ORDER BY gifts_count DESC
        LIMIT 10
        """
        rows = await conn.fetch(query)

        metrics.popular_tariffs = [
            PopularGiftTariff(
                tariff_name=row["tariff_name"],
                gifts_count=row["gifts_count"],
                activated_count=row["activated_count"],
                activation_rate=row["activation_rate"] or 0.0,
            )
            for row in rows
        ]

    async def _load_monthly_gifts(
        self, conn: asyncpg.Connection, metrics: GiftMetrics
    ) -> None:
        """Загружает динамику подарков по месяцам."""
        query = """
        SELECT
            DATE_TRUNC('month', created_at) as month,
            COUNT(*) as gifts_count,
            COUNT(*) FILTER (WHERE used_at IS NOT NULL) as activated_count,
            ROUND(
                COUNT(*) FILTER (WHERE used_at IS NOT NULL)::numeric /
                NULLIF(COUNT(*), 0) * 100,
                2
            ) as activation_rate
        FROM gift_links
        GROUP BY 1
        ORDER BY 1 DESC
        LIMIT 12
        """
        rows = await conn.fetch(query)

        metrics.monthly_gifts = [
            {
                "month": row["month"],
                "gifts_count": row["gifts_count"],
                "activated_count": row["activated_count"],
                "activation_rate": row["activation_rate"] or 0.0,
            }
            for row in rows
        ]

    async def get_cached(
        self, cache_service, ttl_seconds: int = 300
    ) -> GiftMetrics:
        """Получает метрики с кэшированием.

        Args:
            cache_service: Сервис кэширования
            ttl_seconds: Время жизни кэша в секундах (по умолчанию 5 минут)

        Returns:
            GiftMetrics: Объект с метриками
        """
        from datetime import timedelta

        cache_key = "gift_metrics"
        cached = await cache_service.storage.get("analytics", cache_key)

        if cached is not None:
            logger.debug("Gift metrics loaded from cache")
            return self._dict_to_metrics(cached)

        # Вычисляем из БД
        metrics = await self.get_all_gift_metrics()

        # Кэшируем
        await cache_service.storage.set(
            "analytics",
            cache_key,
            self._metrics_to_dict(metrics),
            timedelta(seconds=ttl_seconds),
        )
        logger.debug(f"Gift metrics computed and cached for {ttl_seconds}s")

        return metrics

    @staticmethod
    def _metrics_to_dict(metrics: GiftMetrics) -> dict:
        """Сериализует метрики в dict для кэширования."""
        return {
            "overview": (
                {
                    "total_gifts": metrics.overview.total_gifts,
                    "total_senders": metrics.overview.total_senders,
                    "total_recipients": metrics.overview.total_recipients,
                    "activated_count": metrics.overview.activated_count,
                    "not_activated_count": metrics.overview.not_activated_count,
                    "activation_rate": metrics.overview.activation_rate,
                }
                if metrics.overview
                else None
            ),
            "activation_stats": (
                {
                    "avg_activation_hours": metrics.activation_stats.avg_activation_hours,
                    "median_activation_hours": metrics.activation_stats.median_activation_hours,
                    "activated_within_24h": metrics.activation_stats.activated_within_24h,
                    "activated_within_week": metrics.activation_stats.activated_within_week,
                }
                if metrics.activation_stats
                else None
            ),
            "popular_tariffs": [
                {
                    "tariff_name": t.tariff_name,
                    "gifts_count": t.gifts_count,
                    "activated_count": t.activated_count,
                    "activation_rate": t.activation_rate,
                }
                for t in metrics.popular_tariffs
            ],
            "monthly_gifts": [
                {
                    "month": m["month"].isoformat() if m["month"] else None,
                    "gifts_count": m["gifts_count"],
                    "activated_count": m["activated_count"],
                    "activation_rate": m["activation_rate"],
                }
                for m in metrics.monthly_gifts
            ],
        }

    @staticmethod
    def _dict_to_metrics(data: dict) -> GiftMetrics:
        """Десериализует dict обратно в GiftMetrics."""
        from datetime import datetime

        metrics = GiftMetrics()

        # Восстанавливаем overview
        if data.get("overview"):
            o = data["overview"]
            metrics.overview = GiftOverview(
                total_gifts=o["total_gifts"],
                total_senders=o["total_senders"],
                total_recipients=o["total_recipients"],
                activated_count=o["activated_count"],
                not_activated_count=o["not_activated_count"],
                activation_rate=o["activation_rate"],
            )

        # Восстанавливаем статистику активации
        if data.get("activation_stats"):
            a = data["activation_stats"]
            metrics.activation_stats = GiftActivationStats(
                avg_activation_hours=a["avg_activation_hours"],
                median_activation_hours=a["median_activation_hours"],
                activated_within_24h=a["activated_within_24h"],
                activated_within_week=a["activated_within_week"],
            )

        # Восстанавливаем популярные тарифы
        metrics.popular_tariffs = [
            PopularGiftTariff(
                tariff_name=t["tariff_name"],
                gifts_count=t["gifts_count"],
                activated_count=t["activated_count"],
                activation_rate=t["activation_rate"],
            )
            for t in data["popular_tariffs"]
        ]

        # Восстанавливаем динамику по месяцам
        metrics.monthly_gifts = [
            {
                "month": datetime.fromisoformat(m["month"]) if m["month"] else None,
                "gifts_count": m["gifts_count"],
                "activated_count": m["activated_count"],
                "activation_rate": m["activation_rate"],
            }
            for m in data["monthly_gifts"]
        ]

        return metrics
