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
            logger.warning("bot_token not configured, skipping send_message", chat_id=chat_id)
            return
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            logger.warning("Invalid chat_id for Telegram send_message", chat_id=chat_id)
            return
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        reply_markup = kwargs.get("reply_markup")
        if reply_markup is not None:
            # InlineKeyboardMarkup from aiogram supports .model_dump() via pydantic
            try:
                payload["reply_markup"] = reply_markup.model_dump(exclude_none=True)
            except AttributeError:
                payload["reply_markup"] = reply_markup
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._base_url}/sendMessage", json=payload)
                if resp.status_code != 200:
                    body = resp.text[:300]
                    logger.warning(
                        f"Telegram sendMessage failed: status={resp.status_code} body={body}",
                        chat_id=chat_id,
                    )
        except Exception as e:
            logger.error("Telegram sendMessage error", extra={"chat_id": chat_id, "error": str(e)})

    async def get_chat_member(self, chat_id: str, user_id: int) -> dict | None:
        """Проверяет статус пользователя в чате через Bot API getChatMember.

        Возвращает result-объект (с полем ``status``) при ``{ok:true}``.
        Возвращает ``None`` при network error / не-200 / ``{ok:false}`` /
        пустом токене — это сигнал «не удалось проверить», НЕ «не подписан».
        Не-участник приходит как ``{status: "left"}`` (ok=true) — это валидный
        результат, НЕ None.
        """
        if not self._token:
            logger.warning("bot_token not configured, skipping get_chat_member")
            return None
        try:
            cid = int(user_id)
        except (ValueError, TypeError):
            logger.warning("Invalid user_id for get_chat_member", user_id=user_id)
            return None
        payload = {"chat_id": chat_id, "user_id": cid}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{self._base_url}/getChatMember", json=payload)
                if resp.status_code != 200:
                    logger.warning(
                        f"Telegram getChatMember failed: status={resp.status_code} body={resp.text[:300]}",
                        chat_id=chat_id, user_id=cid,
                    )
                    return None
                data = resp.json()
                if not data.get("ok"):
                    logger.warning(
                        f"Telegram getChatMember not ok: {data.get('description')}",
                        chat_id=chat_id, user_id=cid,
                    )
                    return None
                return data.get("result")
        except Exception as e:
            logger.error(
                "Telegram getChatMember error",
                extra={"chat_id": chat_id, "user_id": cid, "error": str(e)},
            )
            return None

    async def send_document(self, *args: object, **kwargs: object) -> None:
        pass

    async def send_photo(self, *args: object, **kwargs: object) -> None:
        pass


bot = _TelegramBot(settings.bot_token)
