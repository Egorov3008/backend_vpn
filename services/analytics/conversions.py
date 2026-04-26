"""Сервис расчёта конверсий пользователей через SQL-агрегаты.

Оптимизированная версия с использованием CTE-запросов для агрегации на стороне БД.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List

import asyncpg

from logger import logger


@dataclass
class TariffStat:
    """Статистика по тарифу."""
    tariff_name: str
    payment_count: int
    total_amount: float


@dataclass
class ConversionMetrics:
    """Метрики конверсий."""
    year: int = 0

    # === База пользователей (всего) ===
    total_users: int = 0
    users_with_keys: int = 0
    users_with_active_keys: int = 0
    trial_keys_active: int = 0
    paid_keys_active: int = 0

    # === Регистрации ===
    registered_this_year: int = 0
    registered_this_month: int = 0
    registered_this_week: int = 0

    # === Воронка конверсий (за год) ===
    # Шаг 1: Регистрация → Trial
    trial_activated_this_year: int = 0
    reg_to_trial_pct: float = 0.0

    # Шаг 2: Trial → Оплата
    trial_to_paid_this_year: int = 0
    trial_to_paid_pct: float = 0.0

    # Шаг 3: Удержание
    payers_this_year: int = 0
    repeat_payers_this_year: int = 0
    retention_pct: float = 0.0

    # Итоговая конверсия новый пользователь → оплата
    overall_conversion_pct: float = 0.0

    # === Каналы привлечения (за год) ===
    referred_this_year: int = 0
    referred_paid_this_year: int = 0
    referral_pct: float = 0.0

    gifts_this_year: int = 0
    gifts_activated_this_year: int = 0
    gift_pct: float = 0.0

    # === Топ тарифов (за год) ===
    tariff_stats: List[TariffStat] = field(default_factory=list)
    total_revenue_this_year: float = 0.0


def _pct(numerator: int, denominator: int) -> float:
    """Вычисляет процент с защитой от деления на ноль."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


class ConversionMetricsService:
    """Вычисляет метрики конверсий через SQL-агрегаты на стороне БД.

    Использует CTE (Common Table Expressions) для эффективного сбора всех метрик
    в одном запросе с использованием индексов PostgreSQL.
    """

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._db_pool = db_pool

    async def get_all(self) -> ConversionMetrics:
        """Получает все метрики конверсий за текущий год.

        Returns:
            ConversionMetrics: Объект с рассчитанными метриками
        """
        now = datetime.now(timezone.utc)
        year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        week_ago = now - timedelta(days=7)

        async with self._db_pool.acquire() as conn:
            # Основной CTE-запрос для всех агрегированных метрик
            metrics_row = await self._fetch_base_metrics(
                conn, year_start, month_start, week_ago, now
            )
            # Отдельный запрос для тарифов (требует GROUP BY)
            tariff_stats = await self._fetch_tariff_stats(conn, year_start)

        m = ConversionMetrics(year=now.year)

        # Заполняем из результатов запроса
        m.total_users = metrics_row["total_users"]
        m.users_with_keys = metrics_row["users_with_keys"]
        m.users_with_active_keys = metrics_row["users_with_active_keys"]
        m.trial_keys_active = metrics_row["trial_keys_active"]
        m.paid_keys_active = metrics_row["paid_keys_active"]

        m.registered_this_year = metrics_row["reg_year"]
        m.registered_this_month = metrics_row["reg_month"]
        m.registered_this_week = metrics_row["reg_week"]

        m.trial_activated_this_year = metrics_row["trial_activated"]
        m.reg_to_trial_pct = _pct(
            metrics_row["trial_activated"], metrics_row["reg_year"]
        )

        m.trial_to_paid_this_year = metrics_row["trial_to_paid"]
        m.trial_to_paid_pct = _pct(
            metrics_row["trial_to_paid"], metrics_row["trial_activated"]
        )

        m.payers_this_year = metrics_row["payers_this_year"]
        m.repeat_payers_this_year = metrics_row["repeat_payers"]
        m.retention_pct = _pct(
            metrics_row["repeat_payers"], metrics_row["payers_this_year"]
        )

        m.overall_conversion_pct = _pct(
            metrics_row["trial_to_paid"], metrics_row["reg_year"]
        )

        m.referred_this_year = metrics_row["referred_this_year"]
        m.referred_paid_this_year = metrics_row["referred_paid_this_year"]
        m.referral_pct = _pct(
            metrics_row["referred_paid_this_year"], metrics_row["referred_this_year"]
        )

        m.gifts_this_year = metrics_row["gifts_this_year"]
        m.gifts_activated_this_year = metrics_row["gifts_activated_this_year"]
        m.gift_pct = _pct(
            metrics_row["gifts_activated_this_year"], metrics_row["gifts_this_year"]
        )

        m.tariff_stats = tariff_stats
        m.total_revenue_this_year = sum(t.total_amount for t in tariff_stats)

        return m

    async def _fetch_base_metrics(
        self,
        conn: asyncpg.Connection,
        year_start: datetime,
        month_start: datetime,
        week_ago: datetime,
        now: datetime,
    ) -> asyncpg.Record:
        """Выполняет CTE-запрос для базовых метрик.
        
        Примечание: поле amount в таблице keys может отсутствовать в старых БД.
        Trial ключи определяются по отсутствию успешных платежей у пользователя.
        """
        query = """
        WITH
        -- Активные ключи (expiry_time > now)
        active_keys AS (
            SELECT DISTINCT tg_id
            FROM keys
            WHERE expiry_time > $4
        ),
        -- Пользователи с ключами
        users_with_keys AS (
            SELECT COUNT(DISTINCT k.tg_id) as count
            FROM active_keys k
        ),
        -- Пользователи с активными ключами
        users_with_active_keys AS (
            SELECT COUNT(DISTINCT k.tg_id) as count
            FROM active_keys k
            INNER JOIN users u ON k.tg_id = u.tg_id
            WHERE NOT u.is_blocked
        ),
        -- Пользователи с успешными платежами (для определения платных ключей)
        users_with_payments AS (
            SELECT DISTINCT tg_id
            FROM payments
            WHERE status = 'succeeded'
        ),
        -- Trial и платные ключи
        key_types AS (
            SELECT
                COUNT(*) FILTER (WHERE NOT EXISTS (
                    SELECT 1 FROM users_with_payments uwp WHERE uwp.tg_id = ak.tg_id
                )) as trial_keys,
                COUNT(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM users_with_payments uwp WHERE uwp.tg_id = ak.tg_id
                )) as paid_keys
            FROM active_keys ak
        ),
        -- Регистрации по периодам
        registrations AS (
            SELECT
                COUNT(*) FILTER (WHERE created_at >= $1 AND NOT is_blocked) as reg_year,
                COUNT(*) FILTER (WHERE created_at >= $2 AND NOT is_blocked) as reg_month,
                COUNT(*) FILTER (WHERE created_at >= $3 AND NOT is_blocked) as reg_week
            FROM users
        ),
        -- Воронка: trial активированные в этом году
        trial_activated AS (
            SELECT COUNT(*) as count
            FROM users
            WHERE NOT is_blocked
              AND created_at >= $1
              AND trial > 0
        ),
        -- Воронка: trial → оплата (пересечение)
        trial_to_paid AS (
            SELECT COUNT(DISTINCT u.tg_id) as count
            FROM users u
            INNER JOIN payments p ON u.tg_id = p.tg_id
            WHERE NOT u.is_blocked
              AND u.created_at >= $1
              AND u.trial > 0
              AND p.status = 'succeeded'
        ),
        -- Платежи по пользователям за год
        payments_by_user AS (
            SELECT tg_id, COUNT(*) as payment_count
            FROM payments
            WHERE status = 'succeeded'
              AND created_at >= $1
            GROUP BY tg_id
        ),
        -- Удержание: пользователи с повторными оплатами
        retention AS (
            SELECT
                COUNT(*) as payers,
                COUNT(*) FILTER (WHERE payment_count >= 2) as repeat_payers
            FROM payments_by_user
        ),
        -- Рефералы зарегистрированные в этом году
        referrals AS (
            SELECT
                COUNT(*) as referred_total,
                COUNT(*) FILTER (
                    WHERE EXISTS (
                        SELECT 1 FROM payments p
                        WHERE p.tg_id = u.tg_id AND p.status = 'succeeded'
                    )
                ) as referred_paid
            FROM users u
            WHERE NOT is_blocked
              AND referral_id IS NOT NULL
              AND created_at >= $1
        ),
        -- Подарки
        gifts AS (
            SELECT
                COUNT(*) as gifts_total,
                COUNT(*) FILTER (WHERE sender_tg_id IS NOT NULL) as gifts_activated
            FROM gift_links
            WHERE created_at >= $1
        ),
        -- Базовые пользователи
        base_users AS (
            SELECT COUNT(*) as total_users
            FROM users
            WHERE NOT is_blocked
        )
        SELECT
            bu.total_users,
            COALESCE(uwk.count, 0) as users_with_keys,
            COALESCE(uwak.count, 0) as users_with_active_keys,
            COALESCE(kt.trial_keys, 0) as trial_keys_active,
            COALESCE(kt.paid_keys, 0) as paid_keys_active,
            COALESCE(r.reg_year, 0) as reg_year,
            COALESCE(r.reg_month, 0) as reg_month,
            COALESCE(r.reg_week, 0) as reg_week,
            COALESCE(ta.count, 0) as trial_activated,
            COALESCE(tp.count, 0) as trial_to_paid,
            COALESCE(ret.payers, 0) as payers_this_year,
            COALESCE(ret.repeat_payers, 0) as repeat_payers,
            COALESCE(ref.referred_total, 0) as referred_this_year,
            COALESCE(ref.referred_paid, 0) as referred_paid_this_year,
            COALESCE(g.gifts_total, 0) as gifts_this_year,
            COALESCE(g.gifts_activated, 0) as gifts_activated_this_year
        FROM base_users bu
        CROSS JOIN users_with_keys uwk
        CROSS JOIN users_with_active_keys uwak
        CROSS JOIN key_types kt
        CROSS JOIN registrations r
        CROSS JOIN trial_activated ta
        CROSS JOIN trial_to_paid tp
        CROSS JOIN retention ret
        CROSS JOIN referrals ref
        CROSS JOIN gifts g
        """
        now_ms = int(now.timestamp() * 1000)
        return await conn.fetchrow(
            query,
            year_start,
            month_start,
            week_ago,
            now_ms,
        )

    async def _fetch_tariff_stats(
        self, conn: asyncpg.Connection, year_start: datetime
    ) -> List[TariffStat]:
        """Получает топ популярных тарифов по количеству активных ключей за год.
        
        Считает количество ключей, созданных в текущем году, сгруппированных по тарифам.
        """
        query = """
        SELECT
            t.name_tariff as tariff_name,
            COUNT(k.client_id) as keys_count,
            COALESCE(SUM(p.amount), 0) as total_amount
        FROM keys k
        INNER JOIN tariff t ON k.tariff_id = t.id
        LEFT JOIN payments p ON k.tg_id = p.tg_id 
            AND p.status = 'succeeded'
            AND p.created_at >= $1
        WHERE k.created_at >= (
            SELECT EXTRACT(EPOCH FROM $1) * 1000
        )
        GROUP BY t.name_tariff, t.id
        ORDER BY keys_count DESC
        LIMIT 8
        """
        rows = await conn.fetch(query, year_start)
        return [
            TariffStat(
                tariff_name=row["tariff_name"] or f"Тариф #{row['tariff_name']}",
                payment_count=row["keys_count"],  # Количество ключей
                total_amount=round(row["total_amount"] or 0.0, 2),
            )
            for row in rows
        ]

    async def get_cached(
        self, cache_service, ttl_seconds: int = 300
    ) -> ConversionMetrics:
        """Получает метрики с кэшированием.

        Args:
            cache_service: Сервис кэширования
            ttl_seconds: Время жизни кэша в секундах (по умолчанию 5 минут)

        Returns:
            ConversionMetrics: Объект с метриками
        """
        from datetime import timedelta

        cache_key = "conversion_metrics"
        cached = await cache_service.storage.get("analytics", cache_key)

        if cached is not None:
            logger.debug("Conversion metrics loaded from cache")
            # Преобразуем dict обратно в ConversionMetrics
            return self._dict_to_metrics(cached)

        # Вычисляем из БД
        metrics = await self.get_all()

        # Кэшируем
        await cache_service.storage.set(
            "analytics",
            cache_key,
            self._metrics_to_dict(metrics),
            timedelta(seconds=ttl_seconds),
        )
        logger.debug(f"Conversion metrics computed and cached for {ttl_seconds}s")

        return metrics

    @staticmethod
    def _metrics_to_dict(metrics: ConversionMetrics) -> dict:
        """Сериализует метрики в dict для кэширования."""
        return {
            "year": metrics.year,
            "total_users": metrics.total_users,
            "users_with_keys": metrics.users_with_keys,
            "users_with_active_keys": metrics.users_with_active_keys,
            "trial_keys_active": metrics.trial_keys_active,
            "paid_keys_active": metrics.paid_keys_active,
            "registered_this_year": metrics.registered_this_year,
            "registered_this_month": metrics.registered_this_month,
            "registered_this_week": metrics.registered_this_week,
            "trial_activated_this_year": metrics.trial_activated_this_year,
            "reg_to_trial_pct": metrics.reg_to_trial_pct,
            "trial_to_paid_this_year": metrics.trial_to_paid_this_year,
            "trial_to_paid_pct": metrics.trial_to_paid_pct,
            "payers_this_year": metrics.payers_this_year,
            "repeat_payers_this_year": metrics.repeat_payers_this_year,
            "retention_pct": metrics.retention_pct,
            "overall_conversion_pct": metrics.overall_conversion_pct,
            "referred_this_year": metrics.referred_this_year,
            "referred_paid_this_year": metrics.referred_paid_this_year,
            "referral_pct": metrics.referral_pct,
            "gifts_this_year": metrics.gifts_this_year,
            "gifts_activated_this_year": metrics.gifts_activated_this_year,
            "gift_pct": metrics.gift_pct,
            "tariff_stats": [
                {
                    "tariff_name": t.tariff_name,
                    "payment_count": t.payment_count,
                    "total_amount": t.total_amount,
                }
                for t in metrics.tariff_stats
            ],
            "total_revenue_this_year": metrics.total_revenue_this_year,
        }

    @staticmethod
    def _dict_to_metrics(data: dict) -> ConversionMetrics:
        """Десериализует dict обратно в ConversionMetrics."""
        metrics = ConversionMetrics(
            year=data["year"],
            total_users=data["total_users"],
            users_with_keys=data["users_with_keys"],
            users_with_active_keys=data["users_with_active_keys"],
            trial_keys_active=data["trial_keys_active"],
            paid_keys_active=data["paid_keys_active"],
            registered_this_year=data["registered_this_year"],
            registered_this_month=data["registered_this_month"],
            registered_this_week=data["registered_this_week"],
            trial_activated_this_year=data["trial_activated_this_year"],
            reg_to_trial_pct=data["reg_to_trial_pct"],
            trial_to_paid_this_year=data["trial_to_paid_this_year"],
            trial_to_paid_pct=data["trial_to_paid_pct"],
            payers_this_year=data["payers_this_year"],
            repeat_payers_this_year=data["repeat_payers_this_year"],
            retention_pct=data["retention_pct"],
            overall_conversion_pct=data["overall_conversion_pct"],
            referred_this_year=data["referred_this_year"],
            referred_paid_this_year=data["referred_paid_this_year"],
            referral_pct=data["referral_pct"],
            gifts_this_year=data["gifts_this_year"],
            gifts_activated_this_year=data["gifts_activated_this_year"],
            gift_pct=data["gift_pct"],
            tariff_stats=[
                TariffStat(
                    tariff_name=t["tariff_name"],
                    payment_count=t["payment_count"],
                    total_amount=t["total_amount"],
                )
                for t in data["tariff_stats"]
            ],
            total_revenue_this_year=data["total_revenue_this_year"],
        )
        return metrics
