"""Воронка «Вернитесь со скидкой»: личный промокод тем, у кого истёк оплаченный ключ.

Кому писать и какой код выпустить, решает ``WinbackOffers``
(services/core/promo_codes/winback.py); здесь только текст, кнопка и отправка.
"""
from datetime import timedelta, timezone
from typing import Optional

from bot_project import bot
from config import settings
from logger import logger
from models.keys.key import Key
from models.users.user import User
from services.core.promo_codes.models import PromoCode
from services.core.promo_codes.repository import PromoCodeRepository
from services.core.promo_codes.winback import WinbackOffers, WinbackPolicy, WinbackRepository, utc_now
from services.notifications.miniapp_buttons import renew_key_button
from services.notifications.models import NotificationContext, NotificationResult
from services.notifications.rate_limiter import RateLimiter
from shared.miniapp_url import miniapp_link

# Срок кода показываем по Москве, как и окно отправки уведомлений (FunnelManager)
_MOSCOW_TZ = timezone(timedelta(hours=3))


class WinbackOfferFunnel:
    funnel_id = "winback_offer"

    def __init__(self, pool, rate_limiter: RateLimiter, offers: Optional[WinbackOffers] = None) -> None:
        self._rate_limiter = rate_limiter
        self._offers = offers or WinbackOffers(
            WinbackPolicy.from_settings(settings),
            WinbackRepository(pool),
            PromoCodeRepository(pool, reservation_ttl_minutes=settings.promo_reservation_ttl_minutes),
        )
        self._targets: dict[int, Key] = {}

    async def should_send(self, ctx: NotificationContext) -> bool:
        key = await self._offers.target_key(ctx.user.tg_id, ctx.keys, utc_now())
        if key is None:
            return False
        self._targets[ctx.user.tg_id] = key
        return True

    async def process(self, ctx: NotificationContext) -> NotificationResult:
        result = NotificationResult(tg_id=ctx.user.tg_id, funnel_id=self.funnel_id)
        key = self._targets.pop(ctx.user.tg_id, None)
        if key is None:
            result.skipped += 1
            return result

        now = utc_now()
        cohort = self._offers.cohort(ctx.user.tg_id)
        promo = await self._offers.issue_code(ctx.user.tg_id, now) if cohort == "offer" else None
        days_ago = max(1, int((now.timestamp() * 1000 - key.expiry_time) // 86_400_000))
        if promo is not None:
            text, keyboard = self._offer_message(key, promo, days_ago), self._offer_keyboard(key, promo)
        else:
            text, keyboard = self._control_message(key, days_ago), {"inline_keyboard": [[renew_key_button(key.email)]]}

        send_result = await self._send_safe(ctx.user, text, keyboard)
        # Пишем и недоставленное: иначе каждый час выпускался бы новый код для заблокировавшего бота
        await self._offers.record(ctx.user.tg_id, cohort, promo, key, delivered=send_result == "sent")
        if send_result == "sent":
            result.sent += 1
            logger.info("Акция возврата отправлена", tg_id=ctx.user.tg_id, cohort=cohort)
        elif send_result == "blocked":
            result.failed_blocked += 1
        else:
            result.failed_other += 1
        return result

    def _offer_message(self, key: Key, promo: PromoCode, days_ago: int) -> str:
        until = promo.ends_at.astimezone(_MOSCOW_TZ)
        months = f"на {promo.max_months} мес." if promo.max_months else "на весь срок оплаты"
        return (
            f"🎁 <b>Возвращайтесь со скидкой {promo.discount_percent}%</b>\n\n"
            f"Ваш ключ {key.email} истёк {days_ago} дн. назад.\n"
            f"Промокод <code>{promo.code}</code> даёт −{promo.discount_percent}% {months}\n"
            f"Действует до {until:%d.%m %H:%M} (МСК), только для вас."
        )

    @staticmethod
    def _offer_keyboard(key: Key, promo: PromoCode) -> dict:
        if settings.miniapp_url:
            url = miniapp_link(settings.miniapp_url, screen="pay", renew=key.email, promo=promo.code)
            return {"inline_keyboard": [[{"text": "🔄 Продлить со скидкой", "web_app": {"url": url}}]]}
        return {"inline_keyboard": [[renew_key_button(key.email)]]}

    @staticmethod
    def _control_message(key: Key, days_ago: int) -> str:
        return (
            f"⛔ <b>Ваш ключ истёк {days_ago} дн. назад</b>\n"
            f"📧 {key.email}\n\n"
            "Продлите доступ — подключение восстановится сразу после оплаты."
        )

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
