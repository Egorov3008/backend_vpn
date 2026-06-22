"""
Протоколы для системы уведомлений.

Следуем Dependency Inversion Principle: сервисы зависят от абстракций,
а не от конкретных реализаций (aiogram Bot).
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class INotifier(ABC):
    """
    Абстракция для отправки уведомлений пользователям.

    Позволяет тестировать платежные сервисы без зависимости от aiogram,
    а также легко менять транспорт уведомлений (Telegram → email → push).
    """

    @abstractmethod
    async def send_key_created(
        self,
        tg_id: int,
        key_data: Dict[str, Any],
    ) -> None:
        """
        Отправить уведомление о создании нового ключа.

        Args:
            tg_id: Telegram ID пользователя
            key_data: Данные ключа (email, public_link, days, etc.)
        """
        pass

    @abstractmethod
    async def send_key_renewed(
        self,
        tg_id: int,
        email: str,
        new_expiry: str,
        tariff_name: str,
        traffic_limit_gb: Optional[int] = None,
    ) -> None:
        """
        Отправить уведомление о продлении ключа.

        Args:
            tg_id: Telegram ID пользователя
            email: Email ключа
            new_expiry: Новая дата истечения (ISO format)
            tariff_name: Название тарифа
            traffic_limit_gb: Лимит трафика в GB (опционально; все ключи безлимитные)
        """
        pass

    @abstractmethod
    async def send_payment_received(
        self,
        tg_id: int,
        amount: float,
        payment_id: str,
    ) -> None:
        """
        Отправить уведомление о получении платежа.

        Args:
            tg_id: Telegram ID пользователя
            amount: Сумма платежа
            payment_id: ID платежа
        """
        pass


class NoOpNotifier(INotifier):
    """
    Null-object паттерн: notifier который ничего не делает.

    Используется по умолчанию когда notifier не настроен,
    чтобы сервисы могли работать без отправки уведомлений.
    """

    async def send_key_created(
        self,
        tg_id: int,
        key_data: Dict[str, Any],
    ) -> None:
        pass

    async def send_key_renewed(
        self,
        tg_id: int,
        email: str,
        new_expiry: str,
        tariff_name: str,
        traffic_limit_gb: Optional[int] = None,
    ) -> None:
        pass

    async def send_payment_received(
        self,
        tg_id: int,
        amount: float,
        payment_id: str,
    ) -> None:
        pass
