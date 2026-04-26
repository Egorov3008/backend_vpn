"""Протоколы для чистой бизнес-логики.

Этот модуль определяет абстрактные интерфейсы, которые позволяют
бизнес-логике не зависеть от конкретных реализаций (Telegram Bot API,
email сервисов, WebSocket и т.д.).
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class NotificationSender(Protocol):
    """Интерфейс для отправки уведомлений пользователям.

    Этот протокол позволяет бизнес-сервисам отправлять уведомления,
    не завися от конкретного канала коммуникации (Telegram, email, SMS).

    Реализации:
    - TelegramNotificationSender — для отправки через Telegram Bot API
    - EmailNotificationSender — для отправки через email (будущее расширение)
    - CompositeNotificationSender — для отправки через несколько каналов
    """

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Optional[Any] = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: Optional[bool] = None,
    ) -> None:
        """Отправить текстовое сообщение пользователю.

        Args:
            chat_id: Идентификатор чата/пользователя
            text: Текст сообщения
            reply_markup: Inline-клавиатура (опционально)
            parse_mode: Формат парсинга ("HTML", "Markdown")
            disable_web_page_preview: Отключить предпросмотр ссылок
        """
        ...

    async def send_photo(
        self,
        chat_id: int,
        photo: Any,
        caption: Optional[str] = None,
        reply_markup: Optional[Any] = None,
        parse_mode: str = "HTML",
    ) -> None:
        """Отправить фотографию пользователю.

        Args:
            chat_id: Идентификатор чата/пользователя
            photo: Фото (файл ID или URL)
            caption: Подпись к фотографии
            reply_markup: Inline-клавиатура (опционально)
            parse_mode: Формат парсинга
        """
        ...


class MessageBuilder(Protocol):
    """Интерфейс для построения клавиатур и сообщений.

    Отделяет логику построения UI-элементов от бизнес-логики.
    """

    def build_support_keyboard(self, support_url: str, profile_callback: str = "profile") -> Any:
        """Построить клавиатуру с кнопками поддержки и профиля.

        Args:
            support_url: URL технической поддержки
            profile_callback: Callback data для кнопки профиля

        Returns:
            InlineKeyboardMarkup — готовая клавиатура
        """
        ...

    def build_renewal_keyboard(self, support_url: str = "https://t.me/support_chat") -> Any:
        """Построить клавиатуру для сообщения о продлении.

        Args:
            support_url: URL технической поддержки

        Returns:
            InlineKeyboardMarkup — готовая клавиатура
        """
        ...
