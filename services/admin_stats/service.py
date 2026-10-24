"""Сборка ответа статистики из независимых секций."""

from typing import Any, Dict, Sequence

import asyncpg

from logger import logger
from services.admin_stats.period import MonthPeriod
from services.admin_stats.sections import (
    Activity,
    NewUsers,
    Payments,
    StatsSection,
    TopTariffs,
    WebSignups,
)


def default_sections() -> list[StatsSection]:
    return [Payments(), Activity(), NewUsers(), WebSignups(), TopTariffs()]


class MonthStatsService:
    """Считает все секции на одном соединении в одной read-only транзакции.

    REPEATABLE READ: все блоки видят один снимок БД, итоги совпадают с рядами по дням.
    """

    def __init__(self, pool: asyncpg.Pool, sections: Sequence[StatsSection] | None = None):
        self.pool = pool
        self.sections = list(sections) if sections is not None else default_sections()

    async def get(self, period: MonthPeriod) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "month": period.first_day.strftime("%Y-%m"),
            "prev_month": period.prev_first_day.strftime("%Y-%m"),
            "tz": period.tz,
            "days_in_month": period.days_in_month,
            "days_elapsed": period.days_elapsed,
        }
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction(isolation="repeatable_read", readonly=True):
                    for section in self.sections:
                        result[section.name] = await section.load(conn, period)
        except Exception as e:
            logger.error("admin_stats: ошибка запросов", error=str(e))
            raise
        return result
