"""Воронка уведомлений об истёкших ключах (grace-период)."""

from datetime import datetime, timedelta
from typing import Optional

from logger import logger
from models.keys.key import Key
from models.users.user import User
from services.notifications.models import NotificationContext, NotificationResult
from services.notifications.rate_limiter import RateLimiter
from services.notifications.dedupe import NotificationDedupeCache
from bot_project import bot

_DEDUPE_TTL = timedelta(hours=24)


class KeyExpiredGraceFunnel:
    """Воронка: «ваш ключ истёк N часов назад» — повтор не чаще 1 раза в 24ч.

    Покрывает ключи, чей expiry_time уже в прошлом и которые не попали
    в окна EXPIRING_24H/EXPIRING_10H (например, после рестарт-шторма scheduler
    или когда ключ пролежал в кэше дольше 24ч).
    """

    funnel_id = "key_expired_grace"

    def __init__(self, pool, rate_limiter: RateLimiter) -> None:
        self._pool = pool
        self._rate_limiter = rate_limiter
        self._dedupe = NotificationDedupeCache(pool)

    async def should_send(self, ctx: NotificationContext) -> bool:
        return bool(ctx.segment_keys)

    async def process(self, ctx: NotificationContext) -> NotificationResult:
        result = NotificationResult(tg_id=ctx.user.tg_id, funnel_id=self.funnel_id)
        for key in ctx.segment_keys:
            if getattr(key, "notified_expired_grace", False):
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
                await self._persist_flag(key)
            elif send_result == "blocked":
                result.failed_blocked += 1
            else:
                result.failed_other += 1
        return result

    @staticmethod
    def _build_text(key: Key) -> str:
        expiry_dt = datetime.fromtimestamp(key.expiry_time / 1000)
        hours_ago = max(0, int((datetime.now() - expiry_dt).total_seconds() // 3600))
        return (
            f"⛔ <b>Ваш ключ истёк {hours_ago} ч. назад</b>\n"
            f"📧 {key.email}\n"
            f"⏳ Истёк: {expiry_dt:%d.%m.%Y %H:%M}\n\n"
            "Продлите доступ — подключение восстановится сразу после оплаты."
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
        key.notified_expired_grace = True

    async def _persist_flag(self, key: Key) -> None:
        """Записать notified_expired_grace=true в БД (выживает после sync_cache)."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "UPDATE keys SET notified_expired_grace = TRUE WHERE email = $1",
                    key.email,
                )
        except Exception as exc:
            logger.error(
                "Не удалось записать notified_expired_grace в БД",
                email=key.email,
                error=str(exc),
            )
