"""Воронка уведомлений об истечении ключей (за 10 часов до окончания)."""

from datetime import datetime, timedelta
from typing import Optional

from logger import logger
from models.keys.key import Key
from models.users.user import User
from services.notifications.models import NotificationContext, NotificationResult
from services.notifications.rate_limiter import RateLimiter
from services.notifications.dedupe import NotificationDedupeCache
from bot_project import bot

_DEDUPE_TTL = timedelta(hours=10)


class KeyExpiryFunnel10h:
    """Воронка: уведомление об истечении ключа за 10 часов."""

    funnel_id = "key_expiry_10h"

    def __init__(self, pool, rate_limiter: RateLimiter) -> None:
        self._rate_limiter = rate_limiter
        self._dedupe = NotificationDedupeCache(pool)

    async def should_send(self, ctx: NotificationContext) -> bool:
        return bool(ctx.segment_keys)

    async def process(self, ctx: NotificationContext) -> NotificationResult:
        result = NotificationResult(tg_id=ctx.user.tg_id, funnel_id=self.funnel_id)
        for key in ctx.segment_keys:
            if key.notified_10h:
                result.skipped += 1
                continue
            dedupe_id = f"{self.funnel_id}:{key.email}"
            if await self._dedupe.is_sent(dedupe_id, ctx.user.tg_id):
                result.skipped += 1
                continue

            text = self._build_text(key)
            keyboard = self._build_keyboard(key)

            send_result = await self._send_safe(ctx.user, text, keyboard)
            if send_result == "sent":
                result.sent += 1
                await self._dedupe.mark_sent(dedupe_id, ctx.user.tg_id, _DEDUPE_TTL)
                await self._mark_notified(key)
            elif send_result == "blocked":
                result.failed_blocked += 1
            else:
                result.failed_other += 1
        return result

    @staticmethod
    def _build_text(key: Key) -> str:
        expiry_dt = datetime.fromtimestamp(key.expiry_time / 1000)
        hours_left = max(0, int((expiry_dt - datetime.now()).total_seconds() // 3600))
        return (
            f"⚠️ <b>Ваш ключ истекает через {hours_left} ч.</b>\n"
            f"📧 {key.email}\n"
            f"⏳ {expiry_dt:%d.%m.%Y %H:%M}\n\n"
            "Продлите доступ, чтобы не потерять подключение к VPN."
        )

    @staticmethod
    def _build_keyboard(key: Key) -> Optional[dict]:
        return {
            "inline_keyboard": [
                [{"text": "🔄 Продлить ключ", "callback_data": f"renew_key|{key.email}"}],
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

    async def _mark_notified(self, key: Key) -> None:
        key.notified_10h = True
