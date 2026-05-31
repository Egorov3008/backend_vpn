"""Воронка реферальных уведомлений."""

from datetime import timedelta
from typing import Optional

from logger import logger
from models.users.user import User
from services.notifications.models import NotificationContext, NotificationResult
from services.notifications.rate_limiter import RateLimiter
from services.notifications.dedupe import NotificationDedupeCache
from bot_project import bot

_DEDUPE_TTL = timedelta(days=3)


class ReferralBonusFunnel:
    """Воронка: приветственное сообщение реферальным пользователям."""

    funnel_id = "referral_bonus"

    def __init__(self, pool, rate_limiter: RateLimiter) -> None:
        self._rate_limiter = rate_limiter
        self._dedupe = NotificationDedupeCache(pool)

    async def should_send(self, ctx: NotificationContext) -> bool:
        if ctx.user.referral_id is None:
            return False
        if ctx.user.trial != 0:
            return False
        return not await self._dedupe.is_sent(self.funnel_id, ctx.user.tg_id)

    async def process(self, ctx: NotificationContext) -> NotificationResult:
        result = NotificationResult(tg_id=ctx.user.tg_id, funnel_id=self.funnel_id)
        text = self._build_text()
        keyboard = self._build_keyboard()

        send_result = await self._send_safe(ctx.user, text, keyboard)
        if send_result == "sent":
            result.sent += 1
            await self._dedupe.mark_sent(self.funnel_id, ctx.user.tg_id, _DEDUPE_TTL)
        elif send_result == "blocked":
            result.failed_blocked += 1
        else:
            result.failed_other += 1
        return result

    @staticmethod
    def _build_text() -> str:
        return (
            "🎉 <b>Вас пригласил друг!</b>\n\n"
            "Активируйте пробный период и получите:\n\n"
            "🆓 <b>7 дней бесплатного пользования</b>\n"
            "📦 <b>10 ГБ трафика</b>\n"
            "⚡️ <b>Полный доступ ко всем функциям</b>\n\n"
            "Начните прямо сейчас! 👇"
        )

    @staticmethod
    def _build_keyboard() -> Optional[dict]:
        return {
            "inline_keyboard": [
                [{"text": "🎁 Активировать пробный период", "callback_data": "activate_stock"}],
                [{"text": "👤 Личный кабинет", "callback_data": "profile"}],
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
