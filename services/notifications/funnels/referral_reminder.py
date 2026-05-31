"""Воронка напоминания о реферальной программе."""

from datetime import timedelta
from typing import Optional

import asyncpg
from logger import logger
from models.users.user import User
from services.notifications.models import NotificationContext, NotificationResult
from services.notifications.rate_limiter import RateLimiter
from services.notifications.dedupe import NotificationDedupeCache
from bot_project import bot

_DEDUPE_TTL = timedelta(days=7)
_CHECK_SQL = "SELECT EXISTS(SELECT 1 FROM referral_links WHERE referrer_tg_id = $1)"


class ReferralReminderFunnel:
    """Воронка: напоминание о реферальной программе пользователям без реф. ссылки."""

    funnel_id = "referral_reminder"

    def __init__(self, pool: asyncpg.Pool, rate_limiter: RateLimiter) -> None:
        self._pool = pool
        self._rate_limiter = rate_limiter
        self._dedupe = NotificationDedupeCache(pool)

    async def should_send(self, ctx: NotificationContext) -> bool:
        if await self._dedupe.is_sent(self.funnel_id, ctx.user.tg_id):
            return False
        async with self._pool.acquire() as conn:
            has_link: bool = await conn.fetchval(_CHECK_SQL, ctx.user.tg_id)
        return not has_link

    async def process(self, ctx: NotificationContext) -> NotificationResult:
        result = NotificationResult(tg_id=ctx.user.tg_id, funnel_id=self.funnel_id)
        send_result = await self._send_safe(ctx.user, self._build_text(), self._build_keyboard())
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
            "🤝 <b>Приглашайте друзей — зарабатывайте вместе!</b>\n\n"
            "У вас есть реферальная программа, которую вы ещё не попробовали 😊\n\n"
            "Вот как это работает:\n\n"
            "🔗 <b>Поделитесь своей уникальной ссылкой</b> с другом\n"
            "💰 <b>Получите 10% от его первого платежа</b> — автоматически на баланс\n"
            "🎁 <b>Друг получит бонус</b> при регистрации по вашей ссылке\n\n"
            "Никаких сложностей — просто скопируйте ссылку и отправьте другу. "
            "Всё остальное бот сделает сам! 👇"
        )

    @staticmethod
    def _build_keyboard() -> Optional[dict]:
        return {
            "inline_keyboard": [
                [{"text": "🤝 Реферальная программа", "callback_data": "open_referral"}],
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
