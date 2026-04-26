"""
Менеджер ключей кеша - единая точка истины для всех ключей.

Гарантирует, что LoadingService, BaseData и остальной код используют одинаковые ключи.
"""

from typing import Union


class CacheKeyManager:
    """
    Генерирует ключи кеша для всех сущностей.

    Правила:
    - Основные ключи используют первичные идентификаторы конкретных моделей
    - Временные ключи: имеют префикс temporary_{entity}_
    - Специальные: для регистрации, активации подарков и т.д.

    ВНИМАНИЕ: Идентификаторы соответствуют полям моделей (@models):
    - User: tg_id
    - Key: email
    - Server: id
    - Tariff: id
    - GiftLink: id или token
    - Inbound: (server_id, inbound_id)
    - PaymentModel: payment_id
    - Stock: tg_id
    """

    # === ОСНОВНЫЕ КЛЮЧИ ===

    @staticmethod
    def user(tg_id: Union[int, str]) -> str:
        """Ключ пользователя по tg_id"""
        return f"user_{tg_id}"

    @staticmethod
    def key(email: str) -> str:
        """Ключ VPN-ключа по email"""
        return f"key_{email}"

    @staticmethod
    def server(server_id: Union[int, str]) -> str:
        """Ключ сервера по id"""
        return f"server_{server_id}"

    @staticmethod
    def tariff(tariff_id: Union[int, str]) -> str:
        """Ключ тарифа по id"""
        return f"tariff_{tariff_id}"

    @staticmethod
    def gift(gift_id: Union[int, str]) -> str:
        """Ключ подарка по id (или token)"""
        return f"gift_{gift_id}"

    @staticmethod
    def inbound(server_id: Union[int, str], inbound_id: Union[int, str]) -> str:
        """Ключ inbound'а по (server_id, inbound_id)"""
        return f"inbound_{server_id}_{inbound_id}"

    @staticmethod
    def payment(payment_id: str) -> str:
        """Ключ платежа по payment_id"""
        return f"payment_{payment_id}"

    @staticmethod
    def stock(tg_id: Union[int, str]) -> str:
        """Ключ скидки по tg_id (один сток на пользователя)"""
        return f"stock_{tg_id}"

    @staticmethod
    def referral_link(token: str) -> str:
        """Ключ реферальной ссылки по token"""
        return f"referral_link_{token}"

    @staticmethod
    def referral_reward(reward_id: Union[int, str]) -> str:
        """Ключ реферальной награды по id"""
        return f"referral_reward_{reward_id}"

    # === СПЕЦИАЛЬНЫЕ КЛЮЧИ ===

    @staticmethod
    def registration_user(tg_id: Union[int, str]) -> str:
        """Ключ для отслеживания заявок на регистрацию (защита от спама)"""
        return f"temporary_registration_user_{tg_id}"

    @staticmethod
    def gift_activation(tg_id: Union[int, str]) -> str:
        """Ключ для хранения данных при активации подарка"""
        return f"from_gift_{tg_id}"

    # === ВРЕМЕННЫЕ КЛЮЧИ (с TTL) ===

    @staticmethod
    def temporary_payment_data(tg_id: Union[int, str]) -> str:
        """Ключ для временного хранения данных платежа (TTL: 10 минут)"""
        return f"temporary_payment_{tg_id}"

    @staticmethod
    def temporary_tariff_data(tg_id: Union[int, str]) -> str:
        """Ключ для временного хранения данных тарифа перед оплатой (TTL: 10 минут)"""
        return f"temporary_tariff_{tg_id}"

    @staticmethod
    def temporary_inbound(tg_id: Union[int, str]) -> str:
        """Ключ для временного хранения inbound_id пользователя"""
        return f"temporary_inbound_{tg_id}"

    # === ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ===

    @staticmethod
    def extract_id(key: str) -> Union[int, str, None]:
        """Извлечение ID из ключа (обратная операция)"""
        parts = key.split("_")
        if len(parts) >= 2:
            return "_".join(parts[1:])  # Для ключей, где ID может содержать _
        return None

    @staticmethod
    def is_temporary(key: str) -> bool:
        """Проверка, является ли ключ временным"""
        return key.startswith("temporary_")


# === МАППИНГ МОДЕЛЕЙ НА МЕТОДЫ КЛЮЧЕЙ ===
# ВАЖНО: Не используется как функция, т.к. разные методы требуют разные параметры
# User.tg_id → CacheKeyManager.user(tg_id)
# Key.email → CacheKeyManager.key(email)
# Server.id → CacheKeyManager.server(id)
# Tariff.id → CacheKeyManager.tariff(id)
# GiftLink.id → CacheKeyManager.gift(id)
# Inbound(server_id, inbound_id) → CacheKeyManager.inbound(server_id, inbound_id)
# PaymentModel.payment_id → CacheKeyManager.payment(payment_id)
# Stock.tg_id → CacheKeyManager.stock(tg_id)
# ReferralLink.token → CacheKeyManager.referral_link(token)
# ReferralReward.id → CacheKeyManager.referral_reward(id)
