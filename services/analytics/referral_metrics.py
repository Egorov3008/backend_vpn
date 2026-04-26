"""Сервис метрик реферальной программы.

Предоставляет метрики эффективности реферальной системы:
- Общая статистика рефералов
- Топ рефереров
- Конверсия рефералов в оплаты
- Доход от рефералов
"""

from dataclasses import dataclass, field
from typing import List, Optional

import asyncpg

from logger import logger


@dataclass
class ReferralOverview:
    """Общая статистика реферальной программы."""
    total_referrers: int  # Количество пользователей с реферальными ссылками
    total_referred: int  # Всего привлечённых рефералов
    referred_with_keys: int  # Рефералы с активными ключами
    referred_paying: int  # Рефералы с оплатами
    conversion_to_keys: float  # Конверсия в ключи (%)
    conversion_to_paid: float  # Конверсия в оплаты (%)


@dataclass
class TopReferrer:
    """Топ реферера."""
    referrer_tg_id: int
    referred_count: int  # Количество рефералов
    paying_referrals: int  # Платящих рефералов
    total_revenue: float  # Общий доход от рефералов
    conversion_rate: float  # Конверсия рефералов в оплату (%)


@dataclass
class ReferralTariffStats:
    """Статистика по тарифам рефералов."""
    tariff_name: str
    referred_count: int
    paying_count: int
    total_revenue: float


@dataclass
class ReferralMetrics:
    """Сводные метрики реферальной программы."""
    # Общая статистика
    overview: Optional[ReferralOverview] = None
    
    # Топ рефереров
    top_referrers: List[TopReferrer] = field(default_factory=list)
    
    # Статистика по тарифам
    tariff_stats: List[ReferralTariffStats] = field(default_factory=list)
    
    # Доход от рефералов
    total_revenue: float = 0.0
    avg_revenue_per_referrer: float = 0.0


class ReferralMetricsService:
    """Сервис для получения метрик реферальной программы."""

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._db_pool = db_pool

    async def get_all_referral_metrics(self, limit: int = 10) -> ReferralMetrics:
        """Получает все метрики реферальной программы.

        Args:
            limit: Лимит топ рефереров (по умолчанию 10)

        Returns:
            ReferralMetrics: Объект с метриками
        """
        metrics = ReferralMetrics()

        async with self._db_pool.acquire() as conn:
            # Общая статистика
            await self._load_overview(conn, metrics)
            
            # Топ рефереров
            await self._load_top_referrers(conn, metrics, limit)
            
            # Статистика по тарифам
            await self._load_tariff_stats(conn, metrics)
            
            # Общий доход
            await self._load_total_revenue(conn, metrics)

        return metrics

    async def _load_overview(
        self, conn: asyncpg.Connection, metrics: ReferralMetrics
    ) -> None:
        """Загружает общую статистику реферальной программы."""
        query = """
        WITH ref_stats AS (
            SELECT 
                COUNT(DISTINCT rl.referrer_tg_id) as total_referrers,
                COUNT(DISTINCT rr.referred_tg_id) as total_referred
            FROM referral_links rl
            LEFT JOIN referral_redemptions rr ON rl.id = rr.referral_link_id
        ),
        referred_with_keys AS (
            SELECT COUNT(DISTINCT r.referred_tg_id) as referred_with_keys
            FROM referral_redemptions r
            INNER JOIN users u ON r.referred_tg_id = u.tg_id
            INNER JOIN keys k ON u.tg_id = k.tg_id
            WHERE k.expiry_time > EXTRACT(EPOCH FROM NOW()) * 1000
        ),
        referred_paying AS (
            SELECT COUNT(DISTINCT r.referred_tg_id) as referred_paying
            FROM referral_redemptions r
            INNER JOIN payments p ON r.referred_tg_id = p.tg_id
            WHERE p.status = 'succeeded'
        )
        SELECT 
            rs.total_referrers,
            rs.total_referred,
            rwk.referred_with_keys,
            rp.referred_paying
        FROM ref_stats rs
        CROSS JOIN referred_with_keys rwk
        CROSS JOIN referred_paying rp
        """
        row = await conn.fetchrow(query)

        total_referred = row["total_referred"] or 0
        referred_with_keys = row["referred_with_keys"] or 0
        referred_paying = row["referred_paying"] or 0

        metrics.overview = ReferralOverview(
            total_referrers=row["total_referrers"] or 0,
            total_referred=total_referred,
            referred_with_keys=referred_with_keys,
            referred_paying=referred_paying,
            conversion_to_keys=(
                (referred_with_keys / total_referred * 100) if total_referred > 0 else 0.0
            ),
            conversion_to_paid=(
                (referred_paying / total_referred * 100) if total_referred > 0 else 0.0
            ),
        )

    async def _load_top_referrers(
        self, conn: asyncpg.Connection, metrics: ReferralMetrics, limit: int
    ) -> None:
        """Загружает топ рефереров."""
        query = """
        SELECT 
            rl.referrer_tg_id,
            COUNT(DISTINCT rr.referred_tg_id) as referred_count,
            COUNT(DISTINCT p.tg_id) as paying_referrals,
            COALESCE(SUM(p.amount), 0) as total_revenue
        FROM referral_links rl
        LEFT JOIN referral_redemptions rr ON rl.id = rr.referral_link_id
        LEFT JOIN payments p ON rr.referred_tg_id = p.tg_id AND p.status = 'succeeded'
        GROUP BY rl.referrer_tg_id
        ORDER BY referred_count DESC
        LIMIT $1
        """
        rows = await conn.fetch(query, limit)

        metrics.top_referrers = [
            TopReferrer(
                referrer_tg_id=row["referrer_tg_id"],
                referred_count=row["referred_count"],
                paying_referrals=row["paying_referrals"] or 0,
                total_revenue=row["total_revenue"] or 0.0,
                conversion_rate=(
                    (row["paying_referrals"] / row["referred_count"] * 100)
                    if row["referred_count"] > 0
                    else 0.0
                ),
            )
            for row in rows
        ]

    async def _load_tariff_stats(
        self, conn: asyncpg.Connection, metrics: ReferralMetrics
    ) -> None:
        """Загружает статистику по тарифам рефералов."""
        query = """
        SELECT 
            t.name_tariff as tariff_name,
            COUNT(DISTINCT rr.referred_tg_id) as referred_count,
            COUNT(DISTINCT p.tg_id) as paying_count,
            COALESCE(SUM(p.amount), 0) as total_revenue
        FROM referral_links rl
        LEFT JOIN referral_redemptions rr ON rl.id = rr.referral_link_id
        LEFT JOIN users u ON rr.referred_tg_id = u.tg_id
        LEFT JOIN keys k ON u.tg_id = k.tg_id
        LEFT JOIN tariff t ON k.tariff_id = t.id
        LEFT JOIN payments p ON rr.referred_tg_id = p.tg_id AND p.status = 'succeeded'
        GROUP BY t.name_tariff
        ORDER BY referred_count DESC
        """
        rows = await conn.fetch(query)

        metrics.tariff_stats = [
            ReferralTariffStats(
                tariff_name=row["tariff_name"] or "Unknown",
                referred_count=row["referred_count"],
                paying_count=row["paying_count"] or 0,
                total_revenue=row["total_revenue"] or 0.0,
            )
            for row in rows
        ]

    async def _load_total_revenue(
        self, conn: asyncpg.Connection, metrics: ReferralMetrics
    ) -> None:
        """Загружает общий доход от рефералов."""
        query = """
        SELECT 
            COALESCE(SUM(p.amount), 0) as total_revenue,
            COUNT(DISTINCT rl.referrer_tg_id) as total_referrers
        FROM referral_links rl
        LEFT JOIN referral_redemptions rr ON rl.id = rr.referral_link_id
        LEFT JOIN payments p ON rr.referred_tg_id = p.tg_id AND p.status = 'succeeded'
        """
        row = await conn.fetchrow(query)

        metrics.total_revenue = row["total_revenue"] or 0.0
        total_referrers = row["total_referrers"] or 0
        
        if total_referrers > 0:
            metrics.avg_revenue_per_referrer = metrics.total_revenue / total_referrers

    async def get_cached(
        self, cache_service, ttl_seconds: int = 300
    ) -> ReferralMetrics:
        """Получает метрики с кэшированием.

        Args:
            cache_service: Сервис кэширования
            ttl_seconds: Время жизни кэша в секундах (по умолчанию 5 минут)

        Returns:
            ReferralMetrics: Объект с метриками
        """
        from datetime import timedelta

        cache_key = "referral_metrics"
        cached = await cache_service.storage.get("analytics", cache_key)

        if cached is not None:
            logger.debug("Referral metrics loaded from cache")
            return self._dict_to_metrics(cached)

        # Вычисляем из БД
        metrics = await self.get_all_referral_metrics()

        # Кэшируем
        await cache_service.storage.set(
            "analytics",
            cache_key,
            self._metrics_to_dict(metrics),
            timedelta(seconds=ttl_seconds),
        )
        logger.debug(f"Referral metrics computed and cached for {ttl_seconds}s")

        return metrics

    @staticmethod
    def _metrics_to_dict(metrics: ReferralMetrics) -> dict:
        """Сериализует метрики в dict для кэширования."""
        return {
            "overview": (
                {
                    "total_referrers": metrics.overview.total_referrers,
                    "total_referred": metrics.overview.total_referred,
                    "referred_with_keys": metrics.overview.referred_with_keys,
                    "referred_paying": metrics.overview.referred_paying,
                    "conversion_to_keys": metrics.overview.conversion_to_keys,
                    "conversion_to_paid": metrics.overview.conversion_to_paid,
                }
                if metrics.overview
                else None
            ),
            "top_referrers": [
                {
                    "referrer_tg_id": r.referrer_tg_id,
                    "referred_count": r.referred_count,
                    "paying_referrals": r.paying_referrals,
                    "total_revenue": r.total_revenue,
                    "conversion_rate": r.conversion_rate,
                }
                for r in metrics.top_referrers
            ],
            "tariff_stats": [
                {
                    "tariff_name": t.tariff_name,
                    "referred_count": t.referred_count,
                    "paying_count": t.paying_count,
                    "total_revenue": t.total_revenue,
                }
                for t in metrics.tariff_stats
            ],
            "total_revenue": metrics.total_revenue,
            "avg_revenue_per_referrer": metrics.avg_revenue_per_referrer,
        }

    @staticmethod
    def _dict_to_metrics(data: dict) -> ReferralMetrics:
        """Десериализует dict обратно в ReferralMetrics."""
        metrics = ReferralMetrics(
            total_revenue=data["total_revenue"],
            avg_revenue_per_referrer=data["avg_revenue_per_referrer"],
        )

        # Восстанавливаем overview
        if data.get("overview"):
            o = data["overview"]
            metrics.overview = ReferralOverview(
                total_referrers=o["total_referrers"],
                total_referred=o["total_referred"],
                referred_with_keys=o["referred_with_keys"],
                referred_paying=o["referred_paying"],
                conversion_to_keys=o["conversion_to_keys"],
                conversion_to_paid=o["conversion_to_paid"],
            )

        # Восстанавливаем топ рефереров
        metrics.top_referrers = [
            TopReferrer(
                referrer_tg_id=r["referrer_tg_id"],
                referred_count=r["referred_count"],
                paying_referrals=r["paying_referrals"],
                total_revenue=r["total_revenue"],
                conversion_rate=r["conversion_rate"],
            )
            for r in data["top_referrers"]
        ]

        # Восстанавливаем статистику по тарифам
        metrics.tariff_stats = [
            ReferralTariffStats(
                tariff_name=t["tariff_name"],
                referred_count=t["referred_count"],
                paying_count=t["paying_count"],
                total_revenue=t["total_revenue"],
            )
            for t in data["tariff_stats"]
        ]

        return metrics
