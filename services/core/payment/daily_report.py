"""Сводка успешных платежей за интервал — для ежедневной рассылки админам.

Лежит в services/core/payment, а не в services/analytics: пакет analytics
сейчас не импортируется (funnel_analytics тянет несуществующий FunnelType).
"""

from dataclasses import dataclass, field
from datetime import datetime

import asyncpg


@dataclass
class OperationBreakdown:
    """Итоги успешных платежей по типу операции (create_key / renew_key)."""
    operation: str
    count: int
    total: float


@dataclass
class DailyPaymentReport:
    """Сводка успешных платежей с succeeded_at в интервале [period_start, period_end)."""
    period_start: datetime
    period_end: datetime
    count: int = 0
    total: float = 0.0
    by_operation: list[OperationBreakdown] = field(default_factory=list)

    @property
    def avg(self) -> float:
        return self.total / self.count if self.count else 0.0


async def get_daily_report(
    pool: asyncpg.Pool,
    period_start: datetime,
    period_end: datetime,
) -> DailyPaymentReport:
    """Считает успешные платежи, завершённые в интервале.

    Платежи без succeeded_at (исторические, до миграции 20261013) не учитываются.
    """
    query = """
    SELECT
        SPLIT_PART(payment_type, '|', 1) AS operation,
        COUNT(*) AS count,
        COALESCE(SUM(amount), 0) AS total
    FROM payments
    WHERE status = 'succeeded'
      AND succeeded_at >= $1
      AND succeeded_at < $2
    GROUP BY 1
    ORDER BY 1
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, period_start, period_end)

    by_operation = [
        OperationBreakdown(
            operation=row["operation"],
            count=row["count"] or 0,
            total=float(row["total"] or 0.0),
        )
        for row in rows
    ]
    return DailyPaymentReport(
        period_start=period_start,
        period_end=period_end,
        count=sum(op.count for op in by_operation),
        total=sum(op.total for op in by_operation),
        by_operation=by_operation,
    )
