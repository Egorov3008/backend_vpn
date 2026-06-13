"""Воронка уведомлений о milestone'ах рефералов.

Примечание: уведомления о бонусе уже отправляются напрямую из bonus_service.py
при начислении. Эта воронка служит для периодической рассылки с напоминанием
о реферальных достижениях (например, еженедельный дайджест).
"""

from datetime import timedelta
from typing import Optional

import asyncpg
from logger import logger
from models.users.user import User
from services.notifications.models import NotificationContext, NotificationResult
from services.notifications.rate_limiter import RateLimiter
from bot_project import bot


class ReferralMilestonesFunnel:
    """Воронка: периодические напоминания реферерам об их достижениях.

    Отправляется не чаще чем раз в 7 дней активным реферерам
    (у которых есть хотя бы один оплативший реферал).
    """

    funnel_id = "referral_milestones"
    _SEND_INTERVAL = timedelta(days=7)

    def __init__(self, pool: asyncpg.Pool, rate_limiter: RateLimiter) -> None:
        self._pool = pool
        self._rate_limiter = rate_limiter

    async def should_send(self, ctx: NotificationContext) -> bool:
        """Проверяем, можно ли отправить уведомление."""
        # Базовая проверка: у пользователя должен быть referral_id
        if ctx.user.referral_id is None:
            return False

        # Проверяем, есть ли у пользователя оплатившие рефералы
        async with self._pool.acquire() as conn:
            paying_referrals_count = await conn.fetchval(
                """
                SELECT COUNT(*)
                FROM users u
                WHERE u.referral_id = $1
                  AND u.check_referral = TRUE
                """,
                ctx.user.tg_id,
            )
            if not paying_referrals_count or paying_referrals_count == 0:
                return False

            # Проверяем, когда было последнее уведомление
            last_sent = await conn.fetchval(
                """
                SELECT last_notification_time
                FROM notifications
                WHERE tg_id = $1
                  AND notification_type = 'referral_milestones'
                """,
                ctx.user.tg_id,
            )

            if last_sent:
                from datetime import datetime, timezone
                if datetime.now(timezone.utc) - last_sent.replace(tzinfo=timezone.utc) < self._SEND_INTERVAL:
                    return False

            return True

    async def process(self, ctx: NotificationContext) -> NotificationResult:
        result = NotificationResult(tg_id=ctx.user.tg_id, funnel_id=self.funnel_id)

        async with self._pool.acquire() as conn:
            # Получаем статистику по рефералам
            stats = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) as total_referrals,
                    COUNT(*) FILTER (WHERE u.check_referral = TRUE) as paying_referrals
                FROM users u
                WHERE u.referral_id = $1
                """,
                ctx.user.tg_id,
            )

            text = self._build_digest_text(
                total=int(stats["total_referrals"] or 0),
                paying=int(stats["paying_referrals"] or 0),
            )
            keyboard = self._build_keyboard()
            send_result = await self._send_safe(ctx.user, text, keyboard)

            if send_result == "sent":
                result.sent = 1
                # Обновляем время последнего уведомления
                await conn.execute(
                    """
                    INSERT INTO notifications (tg_id, notification_type, last_notification_time)
                    VALUES ($1, 'referral_milestones', NOW())
                    ON CONFLICT (tg_id, notification_type)
                    DO UPDATE SET last_notification_time = NOW()
                    """,
                    ctx.user.tg_id,
                )
            elif send_result == "blocked":
                result.failed_blocked = 1
            else:
                result.failed_other = 1

            return result

    def _build_digest_text(self, total: int, paying: int) -> str:
        """Текст дайджеста реферальных достижений."""
        bonus_earned = paying * 10  # Примерный бонус (10% от средней оплаты)
        return (
            f"📊 <b>Ваша реферальная статистика</b>\n\n"
            f"Всего приглашено: <b>{total}</b> чел.\n"
            f"Оплатили: <b>{paying}</b> чел.\n\n"
            f"💰 Ваш приблизительный доход: <b>~{bonus_earned}₽</b>\n\n"
            "Продолжайте приглашать друзей — каждый платёж приносит вам 10% дохода! 👥"
        )

    @staticmethod
    def _build_keyboard() -> Optional[dict]:
        return {
            "inline_keyboard": [
                [{"text": "📊 Реферальная статистика", "callback_data": "referral_stats"}],
                [{"text": "💰 Мой баланс", "callback_data": "balance"}],
            ]
        }

    async def _send_safe(self, user: User, text: str, keyboard: Optional[dict]) -> str:
        await self._rate_limiter.acquire(user.tg_id)
        try:
            await bot.send_message(user.tg_id, text, reply_markup=keyboard)
            return "sent"
        except Exception as exc:
            error_msg = str(exc).lower()
            if "blocked" in error_msg or "forbidden" in error_msg or "chat not found" in error_msg:
                logger.info("User blocked bot", tg_id=user.tg_id)
                return "blocked"
            logger.error("Send message error", tg_id=user.tg_id, error=str(exc))
            return "error"
