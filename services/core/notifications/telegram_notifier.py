"""
Telegram-реализация INotifier.

Использует httpx для отправки сообщений через Bot API,
не зависит от aiogram — только от конфигурации.
"""
import httpx
from typing import Optional, Dict, Any

from logger import logger
from .protocols import INotifier


class TelegramBotNotifier(INotifier):
    """
    Отправляет уведомления через Telegram Bot API используя httpx.

    Не зависит от aiogram — работает напрямую с HTTP API.
    Это позволяет использовать notifier в backend сервисах
    без зависимости от бота.
    """

    def __init__(
        self,
        bot_token: str,
        support_chat_url: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self._bot_token = bot_token
        self._support_chat_url = support_chat_url
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._timeout = timeout

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Отправить сообщение в Telegram.

        Args:
            chat_id: ID чата для отправки
            text: Текст сообщения (поддерживает HTML)
            reply_markup: Inline keyboard (опционально)
        """
        if not self._bot_token:
            logger.warning(
                "bot_token not configured, skipping send_message",
                chat_id=chat_id,
            )
            return

        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            logger.warning(
                "Invalid chat_id for Telegram send_message",
                chat_id=chat_id,
            )
            return

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/sendMessage",
                    json=payload,
                )
                if resp.status_code != 200:
                    body = resp.text[:300]
                    logger.warning(
                        f"Telegram sendMessage failed: status={resp.status_code} body={body}",
                        chat_id=chat_id,
                    )
        except Exception as e:
            logger.error(
                "Telegram sendMessage error",
                extra={"chat_id": chat_id, "error": str(e)},
            )

    def _build_support_keyboard(self) -> Optional[Dict[str, Any]]:
        """Создать клавиатуру с кнопкой поддержки и профилем."""
        if not self._support_chat_url:
            return None

        return {
            "inline_keyboard": [
                [
                    {
                        "text": "Техническая поддержка",
                        "url": self._support_chat_url,
                    },
                ],
                [
                    {
                        "text": "Личный кабинет",
                        "callback_data": "profile",
                    },
                ],
            ],
        }

    async def send_key_created(
        self,
        tg_id: int,
        key_data: Dict[str, Any],
    ) -> None:
        """
        Отправить уведомление о создании нового ключа.

        Формат сообщения:
        <b>Ссылка внизу - твой новый ключ! Скопируй его:</b>

        {key}

        - Теперь перейди в приложение
        - Нажми ➕!
        - Далее ➡️ 'Добавить из буфера обмена'

        ⏳ Осталось дней: {days} 📅

        <b>-Подробная инструкция внизу</b> 👇
        """
        public_link = key_data.get("public_link", "Недоступно")
        days = key_data.get("days", "Неизвестно")

        message = (
            f"<b>Ссылка внизу - твой новый ключ! Скопируй его:</b>\n\n"
            f"{public_link}\n\n"
            f"- Теперь перейди в приложение \n"
            f"- Нажми ➕!\n"
            f"- Далее ➡️ 'Добавить из буфера обмена'\n\n"
            f"⏳ Осталось дней: {days} 📅\n\n"
            f"<b>-Подробная инструкция внизу</b> 👇"
        )

        keyboard = self._build_support_keyboard()
        await self.send_message(tg_id, message, keyboard)

    async def send_key_renewed(
        self,
        tg_id: int,
        email: str,
        new_expiry: str,
        traffic_limit_gb: int,
        tariff_name: str,
    ) -> None:
        """
        Отправить уведомление о продлении ключа.

        Формат сообщения:
        ✅ Ваш ключ {email} продлён!

        📅 До: {new_expiry}
        По тарифу: {tariff_name}
        """
        keyboard = self._build_support_keyboard()
        if keyboard:
            # Добавляем кнопку поддержки в первый ряд
            keyboard["inline_keyboard"].insert(
                0,
                [
                    {
                        "text": "Техническая поддержка",
                        "url": self._support_chat_url,
                    },
                ],
            )

        message = (
            f"✅ Ваш ключ <a href='https://t.me/VPNBot'>{email}</a> продлён!\n\n"
            f"📅 До: <b>{new_expiry}</b>\n"
            f"По тарифу: <b>{tariff_name}</b>\n"
            f"Трафик: <b>{traffic_limit_gb} GB</b>"
        )

        await self.send_message(tg_id, message, keyboard)

    async def send_payment_received(
        self,
        tg_id: int,
        amount: float,
        payment_id: str,
    ) -> None:
        """
        Отправить уведомление о получении платежа.

        Формат сообщения:
        💰 Платеж получен!
        Сумма: {amount} RUB
        ID: {payment_id}
        """
        message = (
            f"💰 Платеж получен!\n\n"
            f"Сумма: <b>{amount:.2f} RUB</b>\n"
            f"ID: <code>{payment_id}</code>"
        )

        await self.send_message(tg_id, message)
