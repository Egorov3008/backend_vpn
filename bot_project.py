import httpx

from config import settings
from logger import logger


class _TelegramBot:
    """Sends Telegram messages directly via Bot API using httpx."""

    def __init__(self, token: str):
        self._token = token
        self._base_url = f"https://api.telegram.org/bot{token}"

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if not self._token:
            logger.warning("bot_token not configured, skipping send_message", extra={"chat_id": chat_id})
            return
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        reply_markup = kwargs.get("reply_markup")
        if reply_markup is not None:
            # InlineKeyboardMarkup from aiogram supports .model_dump() via pydantic
            try:
                payload["reply_markup"] = reply_markup.model_dump()
            except AttributeError:
                payload["reply_markup"] = reply_markup
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._base_url}/sendMessage", json=payload)
                if resp.status_code != 200:
                    logger.warning(
                        "Telegram sendMessage failed",
                        extra={"chat_id": chat_id, "status": resp.status_code, "body": resp.text[:200]},
                    )
        except Exception as e:
            logger.error("Telegram sendMessage error", extra={"chat_id": chat_id, "error": str(e)})

    async def send_document(self, *args: object, **kwargs: object) -> None:
        pass

    async def send_photo(self, *args: object, **kwargs: object) -> None:
        pass


bot = _TelegramBot(settings.bot_token)
