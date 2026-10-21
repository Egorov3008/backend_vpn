"""Проверка подписки на Telegram-канал (CANALL_URL) для бонуса «+N дней за канал».

Бот проверяет подписку сам (aiogram), а кабинеты (Mini App и веб) ходят в
backend напрямую — для них проверку делает backend через Bot API getChatMember.
"""
from typing import Optional, Protocol

from config import settings
from logger import logger

SUBSCRIBED_STATUSES = frozenset({"member", "administrator", "creator"})


def extract_channel_username(channel_url: Optional[str]) -> Optional[str]:
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


class ChatMemberSource(Protocol):
    async def get_chat_member(self, chat_id: str, user_id: int) -> Optional[dict]: ...


class ChannelSubscriptionChecker:
    def __init__(self, bot: ChatMemberSource, channel_url: Optional[str]):
        self._bot = bot
        self._channel = extract_channel_username(channel_url)

    @property
    def configured(self) -> bool:
        return self._channel is not None

    @property
    def channel_url(self) -> Optional[str]:
        """Публичная ссылка на канал для кнопки «Подписаться» в кабинете.

        Строится из username, разобранного из CANALL_URL, поэтому в ответ
        не попадает произвольная строка из конфига (например, `@name`).
        """
        if not self._channel:
            return None
        return f"https://t.me/{self._channel}"

    async def is_subscribed(self, tg_id: int) -> Optional[bool]:
        """True/False — подписан ли; None — проверить не удалось (канал не
        настроен, Telegram недоступен). None нельзя считать подпиской."""
        if not self._channel:
            return None
        member = await self._bot.get_chat_member(f"@{self._channel}", tg_id)
        if member is None:
            logger.warning("Не удалось проверить подписку на канал", tg_id=tg_id)
            return None
        return member.get("status") in SUBSCRIBED_STATUSES


def get_channel_subscription_checker() -> ChannelSubscriptionChecker:
    """FastAPI-зависимость (подменяется в тестах)."""
    from bot_project import bot

    return ChannelSubscriptionChecker(bot, settings.canall_url)
