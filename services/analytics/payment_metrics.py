"""Сервис метрик платежей для статистики и прогнозирования.

Предоставляет:
- Статистику выручки за год, месяц, неделю
- Прогноз планируемой прибыли на неделю и месяц
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Tuple

import asyncpg

from logger import logger


@dataclass
class RevenueStats:
    """Статистика выручки по периодам."""
    year_total: float = 0.0
    month_total: float = 0.0
    week_total: float = 0.0
    day_total: float = 0.0

    # Дополнительные метрики
    year_payments_count: int = 0
    month_payments_count: int = 0
    week_payments_count: int = 0
    day_payments_count: int = 0

    # Средние значения
    avg_payment_year: float = 0.0
    avg_payment_month: float = 0.0
    avg_payment_week: float = 0.0
    avg_payment_day: float = 0.0


@dataclass
class RevenueForecast:
    """Прогноз выручки."""
    # Прогноз на неделю
    week_forecast: float = 0.0
    week_confidence: float = 0.0  # 0-100%

    # Прогноз на месяц
    month_forecast: float = 0.0
    month_confidence: float = 0.0  # 0-100%

    # Методы прогнозирования
    week_method: str = "none"  # moving_avg, linear_regression, combined
    month_method: str = "none"

    # Исторические данные для справки
    last_4_weeks_avg: float = 0.0
    last_3_months_avg: float = 0.0
    growth_trend: float = 0.0  # процент роста/падения


@dataclass
class WeeklyRevenue:
    """Выручка за одну неделю."""
    week_start: datetime
    week_end: datetime
    total: float
    payments_count: int


@dataclass
class MonthlyRevenue:
    """Выручка за один месяц."""
    month_start: datetime
    month_end: datetime
    total: float
    payments_count: int


class PaymentMetricsService:
    """Сервис для получения статистики и прогнозов платежей."""

    def __init__(self, db_pool: asyncpg.Pool) -> None:
        self._db_pool = db_pool

    async def get_revenue_stats(self) -> RevenueStats:
        """Получает статистику выручки за год, месяц, неделю."""
        stats = RevenueStats()

        async with self._db_pool.acquire() as conn:
            # Выручка за год
            year_query = """
            SELECT
                COALESCE(SUM(amount), 0) as total,
                COUNT(*) as count
            FROM payments
            WHERE status = 'succeeded'
              AND created_at >= DATE_TRUNC('year', NOW())
            """
            row = await conn.fetchrow(year_query)
            stats.year_total = row["total"] or 0.0
            stats.year_payments_count = row["count"] or 0

            # Выручка за месяц
            month_query = """
            SELECT
                COALESCE(SUM(amount), 0) as total,
                COUNT(*) as count
            FROM payments
            WHERE status = 'succeeded'
              AND created_at >= DATE_TRUNC('month', NOW())
            """
            row = await conn.fetchrow(month_query)
            stats.month_total = row["total"] or 0.0
            stats.month_payments_count = row["count"] or 0

            # Выручка за неделю (с понедельника)
            week_query = """
            SELECT
                COALESCE(SUM(amount), 0) as total,
                COUNT(*) as count
            FROM payments
            WHERE status = 'succeeded'
              AND created_at >= DATE_TRUNC('week', NOW())
            """
            row = await conn.fetchrow(week_query)
            stats.week_total = row["total"] or 0.0
            stats.week_payments_count = row["count"] or 0

            # Выручка за сегодня
            day_query = """
            SELECT
                COALESCE(SUM(amount), 0) as total,
                COUNT(*) as count
            FROM payments
            WHERE status = 'succeeded'
              AND created_at >= DATE_TRUNC('day', NOW())
            """
            row = await conn.fetchrow(day_query)
            stats.day_total = row["total"] or 0.0
            stats.day_payments_count = row["count"] or 0

            # Средние чеки
            if stats.year_payments_count > 0:
                stats.avg_payment_year = stats.year_total / stats.year_payments_count
            if stats.month_payments_count > 0:
                stats.avg_payment_month = stats.month_total / stats.month_payments_count
            if stats.week_payments_count > 0:
                stats.avg_payment_week = stats.week_total / stats.week_payments_count
            if stats.day_payments_count > 0:
                stats.avg_payment_day = stats.day_total / stats.day_payments_count

        return stats

    async def forecast_revenue(self) -> RevenueForecast:
        """Прогнозирует выручку на неделю и месяц вперёд.

        Использует комбинированный подход:
        1. Скользящее среднее за последние 4 недели / 3 месяца
        2. Линейная регрессия для определения тренда
        3. Комбинированный прогноз с весами
        """
        forecast = RevenueForecast()

        async with self._db_pool.acquire() as conn:
            # Получаем исторические данные по неделям (последние 8 недель)
            weekly_data = await self._get_weekly_revenue(conn, weeks=8)

            # Получаем исторические данные по месяцам (последние 6 месяцев)
            monthly_data = await self._get_monthly_revenue(conn, months=6)

            if not weekly_data and not monthly_data:
                logger.warning("Недостаточно данных для прогнозирования выручки")
                return forecast

            # Прогноз на неделю
            if weekly_data:
                forecast.week_forecast, forecast.week_confidence, forecast.week_method = (
                    self._forecast_single_value(weekly_data, "week")
                )
                forecast.last_4_weeks_avg = (
                    sum(w.total for w in weekly_data[:4]) / min(len(weekly_data), 4)
                    if weekly_data
                    else 0.0
                )

            # Прогноз на месяц
            if monthly_data:
                forecast.month_forecast, forecast.month_confidence, forecast.month_method = (
                    self._forecast_single_value(monthly_data, "month")
                )
                forecast.last_3_months_avg = (
                    sum(m.total for m in monthly_data[:3]) / min(len(monthly_data), 3)
                    if monthly_data
                    else 0.0
                )

            # Рассчитываем тренд роста
            forecast.growth_trend = self._calculate_growth_trend(weekly_data, monthly_data)

        return forecast

    async def _get_weekly_revenue(
        self, conn: asyncpg.Connection, weeks: int = 8
    ) -> List[WeeklyRevenue]:
        """Получает выручку по неделям за последние N недель."""
        query = """
        WITH weeks AS (
            SELECT
                generate_series(
                    DATE_TRUNC('week', NOW()) - INTERVAL '1 week' * $1,
                    DATE_TRUNC('week', NOW()) - INTERVAL '1 week',
                    INTERVAL '1 week'
                ) as week_start
        ),
        weekly_stats AS (
            SELECT
                w.week_start,
                w.week_start + INTERVAL '7 days' as week_end,
                COALESCE(SUM(p.amount), 0) as total,
                COUNT(p.id) as payments_count
            FROM weeks w
            LEFT JOIN payments p ON
                p.status = 'succeeded'
                AND p.created_at >= w.week_start
                AND p.created_at < w.week_start + INTERVAL '7 days'
            GROUP BY w.week_start
        )
        SELECT week_start, week_end, total, payments_count
        FROM weekly_stats
        ORDER BY week_start DESC
        """
        rows = await conn.fetch(query, weeks)

        return [
            WeeklyRevenue(
                week_start=row["week_start"],
                week_end=row["week_end"],
                total=row["total"] or 0.0,
                payments_count=row["payments_count"] or 0,
            )
            for row in rows
        ]

    async def _get_monthly_revenue(
        self, conn: asyncpg.Connection, months: int = 6
    ) -> List[MonthlyRevenue]:
        """Получает выручку по месяцам за последние N месяцев."""
        query = """
        WITH months AS (
            SELECT
                generate_series(
                    DATE_TRUNC('month', NOW()) - INTERVAL '1 month' * $1,
                    DATE_TRUNC('month', NOW()) - INTERVAL '1 month',
                    INTERVAL '1 month'
                ) as month_start
        ),
        monthly_stats AS (
            SELECT
                m.month_start,
                m.month_start + INTERVAL '1 month' as month_end,
                COALESCE(SUM(p.amount), 0) as total,
                COUNT(p.id) as payments_count
            FROM months m
            LEFT JOIN payments p ON
                p.status = 'succeeded'
                AND p.created_at >= m.month_start
                AND p.created_at < m.month_start + INTERVAL '1 month'
            GROUP BY m.month_start
        )
        SELECT month_start, month_end, total, payments_count
        FROM monthly_stats
        ORDER BY month_start DESC
        """
        rows = await conn.fetch(query, months)

        return [
            MonthlyRevenue(
                month_start=row["month_start"],
                month_end=row["month_end"],
                total=row["total"] or 0.0,
                payments_count=row["payments_count"] or 0,
            )
            for row in rows
        ]

    def _forecast_single_value(
        self,
        data_points: List,
        period_type: str,
    ) -> Tuple[float, float, str]:
        """Прогнозирует следующее значение на основе исторических данных.

        Returns:
            Tuple[forecast_value, confidence_percent, method_name]
        """
        values = [dp.total for dp in data_points]

        if len(values) < 2:
            # Недостаточно данных — возвращаем последнее значение с низкой уверенностью
            return values[0] if values else 0.0, 10.0, "insufficient_data"

        # Метод 1: Скользящее среднее
        moving_avg_window = min(4, len(values))
        moving_avg = sum(values[:moving_avg_window]) / moving_avg_window

        # Метод 2: Линейная регрессия (простая)
        slope, intercept = self._linear_regression(values)
        # Прогноз на следующий период (index = len(values))
        regression_forecast = slope * len(values) + intercept

        # Защита от отрицательных значений
        regression_forecast = max(0.0, regression_forecast)

        # Комбинируем с весами: 60% moving_avg, 40% regression
        combined = 0.6 * moving_avg + 0.4 * regression_forecast

        # Рассчитываем уверенность на основе:
        # 1. Количества точек данных
        # 2. Стабильности данных (низкая дисперсия = высокая уверенность)
        data_confidence = self._calculate_confidence(values)

        # Ограничиваем 10-95%
        confidence = max(10.0, min(95.0, data_confidence))

        return combined, confidence, "combined"

    def _linear_regression(self, values: List[float]) -> Tuple[float, float]:
        """Простая линейная регрессия.

        Returns:
            Tuple[slope, intercept]
        """
        n = len(values)
        if n < 2:
            return 0.0, values[0] if values else 0.0

        # Данные идут от новыхших к старшим, но для регрессии
        # мы хотим предсказывать БУДУЩЕЕ, поэтому:
        # index 0 = самое недавнее, index n-1 = самое старое
        # Прогноз будет на index n (ещё более будущее)
        x = list(range(n))
        y = values  # Не инвертируем — регрессия должна идти от старых к новым

        x_mean = sum(x) / n
        y_mean = sum(y) / n

        # slope = Σ((x - x_mean)(y - y_mean)) / Σ((x - x_mean)²)
        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        denominator = sum((xi - x_mean) ** 2 for xi in x)

        if denominator == 0:
            return 0.0, y_mean

        slope = numerator / denominator
        intercept = y_mean - slope * x_mean

        return slope, intercept

    def _calculate_confidence(self, values: List[float]) -> float:
        """Рассчитывает уверенность прогноза (0-100%).

        Учитывает:
        - Количество точек данных (больше = лучше)
        - Коэффициент вариации (меньше = стабильнее = лучше)
        """
        if len(values) < 2:
            return 10.0

        mean = sum(values) / len(values)
        if mean == 0:
            return 10.0

        # Стандартное отклонение
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std_dev = variance ** 0.5

        # Коэффициент вариации (CV)
        cv = std_dev / mean

        # Преобразуем CV в уверенность (низкий CV = высокая уверенность)
        # CV=0 → 100%, CV=1 → ~50%, CV=2 → ~20%
        stability_score = max(0, 100 * (1 - cv / 2))

        #_score за количество данных (2 точки = 50%, 8+ точек = 100%)
        data_score = min(100, (len(values) / 8) * 100)

        # Комбинируем
        confidence = 0.6 * stability_score + 0.4 * data_score

        return confidence

    def _calculate_growth_trend(
        self,
        weekly_data: List[WeeklyRevenue],
        monthly_data: List[MonthlyRevenue],
    ) -> float:
        """Рассчитывает тренд роста в процентах.

        Положительное значение = рост, отрицательное = падение.
        """
        trends = []

        # Тренд по неделям (сравниваем последние 4 недели с предыдущими 4)
        if len(weekly_data) >= 4:
            recent = sum(w.total for w in weekly_data[:4])
            previous = sum(w.total for w in weekly_data[4:]) if len(weekly_data) > 4 else 0
            if previous > 0:
                weekly_growth = ((recent - previous) / previous) * 100
                trends.append(weekly_growth)

        # Тренд по месяцам (сравниваем последние 3 месяца с предыдущими 3)
        if len(monthly_data) >= 3:
            recent = sum(m.total for m in monthly_data[:3])
            previous = sum(m.total for m in monthly_data[3:]) if len(monthly_data) > 3 else 0
            if previous > 0:
                monthly_growth = ((recent - previous) / previous) * 100
                trends.append(monthly_growth)

        if not trends:
            return 0.0

        return sum(trends) / len(trends)
