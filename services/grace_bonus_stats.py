"""Сервис метрик канального бонуса.

Отдаёт админу факт использования бонуса «+N дней за подписку на канал»
(cumulative + today + yesterday).

Источник: user_promo_claims (promo_id='channel_subscription_bonus').
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import asyncpg

from logger import logger

PROMO_CHANNEL_BONUS = "channel_subscription_bonus"


class GraceBonusStatsService:
    """Считает cumulative + today/yesterday метрики по канальному бонусу."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        yesterday_start = today_start - timedelta(days=1)

        try:
            async with self.pool.acquire() as conn:
                bonus_cumulative = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_promo_claims "
                    "WHERE promo_id = $1",
                    PROMO_CHANNEL_BONUS,
                )
                bonus_today = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_promo_claims "
                    "WHERE promo_id = $1 AND claimed_at >= $2 AND claimed_at < $3",
                    PROMO_CHANNEL_BONUS, today_start, today_end,
                )
                bonus_yesterday = await conn.fetchval(
                    "SELECT COUNT(*) FROM user_promo_claims "
                    "WHERE promo_id = $1 AND claimed_at >= $2 AND claimed_at < $3",
                    PROMO_CHANNEL_BONUS, yesterday_start, today_start,
                )
        except Exception as e:
            logger.error("grace_bonus_stats: ошибка запросов", error=str(e))
            raise

        return {
            "channel_bonus": {
                "cumulative": bonus_cumulative,
                "today": bonus_today,
                "yesterday": bonus_yesterday,
            },
        }
