"""Сборка отчёта «Поведение» из независимых секций."""

from typing import Any, Dict, Sequence

import asyncpg

from logger import logger
from services.behavior.period import BehaviorWindow
from services.behavior.sections import BehaviorSection, default_sections


class BehaviorReportService:
    """Все секции на одном соединении в одной read-only транзакции (один снимок БД)."""

    def __init__(self, pool: asyncpg.Pool, sections: Sequence[BehaviorSection] | None = None):
        self.pool = pool
        self.sections = list(sections) if sections is not None else default_sections()

    async def get(self, window: BehaviorWindow) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "tz": window.tz,
            "days": window.days,
            "start": window.start,
            "end": window.end,
        }
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction(isolation="repeatable_read", readonly=True):
                    for section in self.sections:
                        result[section.name] = await section.load(conn, window)
        except Exception as e:
            logger.error("behavior: ошибка запросов", error=str(e))
            raise
        return result
