"""Сервис метрик grace-периода и канального бонуса.

Отдаёт админу:
- Grace: сейчас в grace (срез), истекли после grace, продлены из grace.
- Канальный бонус: воспользовались подпиской на канал.

Каждая метрика (кроме среза «сейчас в grace») — cumulative + today + yesterday.

Источники:
- keys.grace_expiry / keys.expiry_time (ms) — статус GRACE и момент истечения
  grace выводятся из полей; истории переходов нет, но «истекли после grace»
  выводимо из grace_expiry.
- grace_renewal_log — журнал продлений из grace (миграция 018). Cumulative
  считается с момента внедрения журнала.
- user_promo_claims (promo_id='channel_subscription_bonus') — факт бонуса.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import asyncpg

from logger import logger

PROMO_CHANNEL_BONUS = "channel_subscription_bonus"


class GraceBonusStatsService:
    """Считает cumulative + today/yesterday метрики по grace и канальному бонусу."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def get(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        yesterday_start = today_start - timedelta(days=1)

        # ms-границы для BIGINT-полей keys.expiry_time / keys.grace_expiry
        now_ms = int(now.timestamp() * 1000)
        today_start_ms = int(today_start.timestamp() * 1000)
        today_end_ms = today_start_ms + 86_400_000
        yesterday_start_ms = today_start_ms - 86_400_000

        try:
            async with self.pool.acquire() as conn:
                currently_in_grace = await conn.fetchval(
                    "SELECT COUNT(*) FROM keys "
                    "WHERE grace_expiry IS NOT NULL "
                    "AND expiry_time <= $1 AND grace_expiry > $1",
                    now_ms,
                )

                expired_cumulative = await conn.fetchval(
                    "SELECT COUNT(*) FROM keys "
                    "WHERE grace_expiry IS NOT NULL AND grace_expiry < $1",
                    now_ms,
                )
                expired_today = await conn.fetchval(
                    "SELECT COUNT(*) FROM keys "
                    "WHERE grace_expiry IS NOT NULL "
                    "AND grace_expiry >= $1 AND grace_expiry < $2",
                    today_start_ms, today_end_ms,
                )
                expired_yesterday = await conn.fetchval(
                    "SELECT COUNT(*) FROM keys "
                    "WHERE grace_expiry IS NOT NULL "
                    "AND grace_expiry >= $1 AND grace_expiry < $2",
                    yesterday_start_ms, today_start_ms,
                )

                renewed_cumulative = await conn.fetchval(
                    "SELECT COUNT(*) FROM grace_renewal_log"
                )
                renewed_today = await conn.fetchval(
                    "SELECT COUNT(*) FROM grace_renewal_log "
                    "WHERE occurred_at >= $1 AND occurred_at < $2",
                    today_start, today_end,
                )
                renewed_yesterday = await conn.fetchval(
                    "SELECT COUNT(*) FROM grace_renewal_log "
                    "WHERE occurred_at >= $1 AND occurred_at < $2",
                    yesterday_start, today_start,
                )

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
            "grace": {
                "currently_in_grace": currently_in_grace,
                "expired_after_grace": {
                    "cumulative": expired_cumulative,
                    "today": expired_today,
                    "yesterday": expired_yesterday,
                },
                "renewed_from_grace": {
                    "cumulative": renewed_cumulative,
                    "today": renewed_today,
                    "yesterday": renewed_yesterday,
                },
            },
            "channel_bonus": {
                "cumulative": bonus_cumulative,
                "today": bonus_today,
                "yesterday": bonus_yesterday,
            },
        }