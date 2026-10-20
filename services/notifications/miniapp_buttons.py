"""Кнопки уведомлений, ведущие в Telegram Mini App.

Уведомления уходят в личный чат с ботом, поэтому можно слать inline-кнопки
``web_app``: «Продлить ключ» открывает Mini App сразу на экране оплаты этого
ключа. Пока ``MINIAPP_URL`` не задан, остаётся старый callback, который
обрабатывает бот.
"""

from config import settings
from shared.miniapp_url import miniapp_link


def _web_app(text: str, **params: str) -> dict:
    return {"text": text, "web_app": {"url": miniapp_link(settings.miniapp_url, **params)}}


def renew_key_button(email: str) -> dict:
    """«Продлить ключ» → экран оплаты Mini App с выбранным ключом."""
    if settings.miniapp_url:
        return _web_app("🔄 Продлить ключ", screen="pay", renew=email)
    return {"text": "🔄 Продлить ключ", "callback_data": f"renew_key|{email}"}

