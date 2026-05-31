"""Воронка уведомлений о неиспользованном trial-ключе."""

from datetime import timedelta
from typing import Optional

from logger import logger
from models.keys.key import Key
from models.users.user import User
from services.notifications.models import NotificationContext, NotificationResult
from services.notifications.rate_limiter import RateLimiter
from services.notifications.dedupe import NotificationDedupeCache
from bot_project import bot

_DEDUPE_TTL = timedelta(days=3)


class TrialReminderFunnel:
    """Воронка: напоминание пользователям с неиспользованным trial-ключом."""

    funnel_id = "trial_unused"

    def __init__(self, pool, rate_limiter: RateLimiter) -> None:
        self._rate_limiter = rate_limiter
        self._dedupe = NotificationDedupeCache(pool)

    async def should_send(self, ctx: NotificationContext) -> bool:
        if not ctx.segment_keys:
            return False
        unused = [k for k in ctx.segment_keys if (k.used_traffic or 0.0) == 0.0]
        if not unused:
            return False
        return not await self._dedupe.is_sent(self.funnel_id, ctx.user.tg_id)

    async def process(self, ctx: NotificationContext) -> NotificationResult:
        result = NotificationResult(tg_id=ctx.user.tg_id, funnel_id=self.funnel_id)
        key = next((k for k in ctx.segment_keys if (k.used_traffic or 0.0) == 0.0), None)
        if key is None:
            result.failed_other += 1
            return result

        # key.key уже содержит полный URL подписки (subscription_url/email)
        link_to_connect = key.key
        text = self._build_text(key.email)
        keyboard = self._build_keyboard(link_to_connect, key.key)

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
    def _build_text(email: str) -> str:
        return (
            f"🎁 <b>Ваш ключ {email} активирован!</b>\n\n"
            "Похоже, вы ещё не подключились к VPN. Давайте попробуем вместе?\n\n"
            "1. Скачайте приложение для нашего VPN:\n"
            "2. Согласитесь со всеми изменениями\n"
            "3. Просто нажмите 'Вставить ключ в приложение'\n"
            "Если не получается, напишите нам, мы поможем\n"
        )

    @staticmethod
    def _build_keyboard(link_to_connect: str, copy_text: str) -> Optional[dict]:
        return {
            "inline_keyboard": [
                [{"text": "🚀 Вставить ключ в приложение", "url": link_to_connect}],
                [{"text": "Скопировать ключ", "copy_text": {"text": copy_text}}],
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
