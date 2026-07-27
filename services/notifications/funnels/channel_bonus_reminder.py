"""Воронка «+N дней за подписку на канал» — напоминания не-подписанным.

3 напоминания по якорям 1д/3д/7д от key.created_at, с бёрст-гардом
минимум 1 день между отправками (для старых ключей). Состояние шагов —
в таблице cache (StepState). Подписку проверяет сам фуннель через
bot.get_chat_member. Push-кнопка ведёт в существующий callback
``channel_bonus`` — новых хендлеров в боте нет.
"""

import time
from datetime import timedelta
from typing import Optional

from config import settings
from logger import logger
from models.keys.key import Key
from models.users.user import User
from services.core.keys.utils.status import KeyStatus
from services.core.promotions.channel_bonus_service import is_channel_bonus_claimed
from services.notifications.models import NotificationContext, NotificationResult
from services.notifications.rate_limiter import RateLimiter
from services.notifications.step_state import StepState, MAX_STEPS
from bot_project import bot

# Якоря шагов от key.created_at (мс): шаг 1 @ 1д, шаг 2 @ 3д, шаг 3 @ 7д.
_STEP_OFFSETS_MS = (
    1 * 24 * 60 * 60 * 1000,
    3 * 24 * 60 * 60 * 1000,
    7 * 24 * 60 * 60 * 1000,
)
# Минимальный gap между отправками (защита от бёрста для старых ключей).
_MIN_GAP_MS = 1 * 24 * 60 * 60 * 1000

# TTL состояния: держим между шагами, чтобы помнить прогресс.
_STATE_TTL = timedelta(days=30)
# TTL терминальной записи (всё отправлено / бонус получен).
_TERMINAL_TTL = timedelta(days=30)

_SUBSCRIBED_STATUSES = {"member", "administrator", "creator"}


def _now_ms() -> int:
    """Текущее время в ms. Patchable в тестах."""
    return int(time.time() * 1000)


def _extract_channel_username(channel_url: str | None) -> str | None:
    """Извлекает username канала из URL (тот же парсер, что в боте).

    Поддерживает https://t.me/<name>, https://telegram.me/<name>,
    @<name>, <name>.
    """
    if not channel_url:
        return None
    s = channel_url.strip()
    if s.startswith("https://"):
        s = s[8:]
    elif s.startswith("http://"):
        s = s[7:]
    for domain in ("telegram.me/", "t.me/", "telegram.me", "t.me"):
        if s.startswith(domain):
            s = s[len(domain):]
            break
    if s.startswith("@"):
        s = s[1:]
    s = s.split("?")[0].split("/")[0]
    if s and s.replace("_", "").replace("-", "").isalnum():
        return s
    return None


def _select_anchor_key(keys: list[Key]) -> Optional[Key]:
    """Активный/grace ключ с максимальным expiry_time; None если нет."""
    anchor: Optional[Key] = None
    for k in keys:
        if KeyStatus.of(k) in (KeyStatus.ACTIVE, KeyStatus.GRACE):
            if anchor is None or (k.expiry_time or 0) > (anchor.expiry_time or 0):
                anchor = k
    return anchor


class ChannelBonusReminderFunnel:
    """3 напоминания не-подписанным с active/grace-ключом."""

    funnel_id = "channel_bonus_reminder"

    def __init__(self, pool, rate_limiter: RateLimiter) -> None:
        self._pool = pool
        self._rate_limiter = rate_limiter
        self._state = StepState(pool)

    async def should_send(self, ctx: NotificationContext) -> bool:
        channel_url = getattr(settings, "canall_url", "")
        channel = _extract_channel_username(channel_url)
        if not channel:
            return False

        # 1. cheap: есть active/grace ключ?
        anchor = _select_anchor_key(ctx.keys)
        if anchor is None:
            return False

        # 2. cheap: бонус не получен?
        if await is_channel_bonus_claimed(self._pool, ctx.user.tg_id):
            await self._state.mark_terminal(self.funnel_id, ctx.user.tg_id, _TERMINAL_TTL)
            return False

        # 3. cheap: due по таймингу?
        state = await self._state.get_state(self.funnel_id, ctx.user.tg_id)
        step = int(state.get("step", 0)) if state else 0
        if step >= MAX_STEPS:
            return False
        now = _now_ms()
        if now - (anchor.created_at or 0) < _STEP_OFFSETS_MS[step]:
            return False
        if step > 0:
            last_sent = int(state.get("last_sent_ms", 0) or 0) if state else 0
            if now - last_sent < _MIN_GAP_MS:
                return False

        # 4. expensive: не подписан? (только для дошедших)
        member = await bot.get_chat_member(f"@{channel}", ctx.user.tg_id)
        if member is None:
            # не удалось проверить — skip цикла, шаг не двигаем
            return False
        return member.get("status") not in _SUBSCRIBED_STATUSES

    async def process(self, ctx: NotificationContext) -> NotificationResult:
        result = NotificationResult(tg_id=ctx.user.tg_id, funnel_id=self.funnel_id)
        state = await self._state.get_state(self.funnel_id, ctx.user.tg_id)
        step = int(state.get("step", 0)) if state else 0
        if step >= MAX_STEPS:
            return result

        text = self._build_text(step)
        keyboard = self._build_keyboard()
        send_result = await self._send_safe(ctx.user, text, keyboard)

        if send_result == "sent":
            result.sent += 1
            anchor = _select_anchor_key(ctx.keys)
            key_email = anchor.email if anchor else ""
            new_step = await self._state.advance_step(
                self.funnel_id, ctx.user.tg_id, _now_ms(), key_email, _STATE_TTL
            )
            if new_step >= MAX_STEPS:
                await self._state.mark_terminal(self.funnel_id, ctx.user.tg_id, _TERMINAL_TTL)
        elif send_result == "blocked":
            result.failed_blocked += 1
        else:
            result.failed_other += 1
        return result

    def _build_text(self, step: int) -> str:
        days = getattr(settings, "channel_bonus_days", 7)
        if step == 0:
            return (
                "👋 <b>Подпишитесь на наш канал — получите бонус!</b>\n\n"
                f"Вы уже пользуетесь VPN. Подпишитесь на наш Telegram-канал "
                f"и получите <b>+{days} дней</b> к подписке в подарок. 🎁\n\n"
                "Нажмите кнопку ниже, чтобы забрать бонус."
            )
        if step == 1:
            return (
                "🔔 <b>Напоминаем про бонус +7 дней</b>\n\n"
                f"Подпишитесь на наш канал и получите <b>+{days} дней</b> "
                "к подписке. Предложение всё ещё активно.\n\n"
                "Заберите бонус кнопкой ниже 👇"
            )
        return (
            "⏳ <b>Последнее напоминание про бонус</b>\n\n"
            f"Бонус <b>+{days} дней</b> за подписку на наш канал скоро сгорит. "
            "Подпишитесь и заберите его, пока не поздно!\n\n"
            "Кнопка ниже 👇"
        )

    @staticmethod
    def _build_keyboard() -> Optional[dict]:
        return {
            "inline_keyboard": [
                [{"text": "🎁 Получить +7 дней", "callback_data": "channel_bonus"}],
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