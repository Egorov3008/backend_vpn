"""Журнал деструктивных admin-операций.

Сбой записи не должен рвать операцию — ошибка только логируется (паттерн
grace_renewal_log). Источник: спек 2026-07-06-admin-panel-security-design.md.
"""
from typing import Optional

import asyncpg

from logger import logger
from services.metrics.registry import admin_audit_failures_total


class AuditLogger:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def record(
        self,
        admin_tg_id: Optional[int],
        action: str,
        target: Optional[str],
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO admin_audit_log (admin_tg_id, action, target) "
                    "VALUES ($1, $2, $3)",
                    admin_tg_id,
                    action,
                    target,
                )
        except Exception as e:
            # Метрика观测ability: fail-open глотает сбой, но он должен быть виден
            # в /metrics. Сам инкремент тоже оборачиваем — метрики не должны рвать
            # fail-open аудит (иначе фикс ломает свойство "сбой аудита не рвёт op").
            try:
                admin_audit_failures_total.labels(
                    action=action, error_type=type(e).__name__
                ).inc()
            except Exception:
                pass
            logger.warning(
                "admin_audit_log: insert провален",
                extra={"admin_tg_id": admin_tg_id, "action": action, "error": str(e)},
            )