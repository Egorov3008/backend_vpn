"""
Система уведомлений backend.

Экспортирует протоколы и реализации для отправки уведомлений.
"""
from .protocols import INotifier, NoOpNotifier
from .telegram_notifier import TelegramBotNotifier

__all__ = [
    "INotifier",
    "NoOpNotifier",
    "TelegramBotNotifier",
]
