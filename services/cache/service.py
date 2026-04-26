from typing import TypeVar, Generic, List, Optional, Any
from datetime import timedelta

from models.stocks.stock import Stock
from services.cache.storage import CacheStorage
from services.cache.protocols import CacheProtocol
from models import User, Key, Server, Tariff, GiftLink, Inbound, PaymentModel, ReferralLink

T = TypeVar("T")


class ModelCache(Generic[T]):
    """
    Низкоуровневый внутренний кеш для модели с namespace.
    Реализует CacheProtocol[T].

    ⚠️  ВНУТРЕННИЙ КЛАСС: Используется ТОЛЬКО в CacheService.
    Прямой доступ к методам ModelCache в коде вне CacheService ЗАПРЕЩЕН.
    Всегда обращайтесь к кешу через CacheService.{entity}.{method}().

    Методы:
    - set(key, value, ttl) — установить значение с TTL
    - get(key) — получить значение
    - delete(key) — удалить значение
    - all() — получить все значения namespace
    - keys() — получить все ключи namespace
    - temporary_set(key, ttl, **kwargs) — установить временное значение
    - temporary_get(key) — получить временное значение
    """

    def __init__(self, storage: CacheStorage, namespace: str):
        self.storage = storage
        self.namespace = namespace

    async def set(self, key: str, value: T, ttl: Optional[timedelta] = None) -> None:
        await self.storage.set(self.namespace, key, value, ttl)

    async def get(self, key: str) -> Optional[T]:
        return await self.storage.get(self.namespace, key)

    async def delete(self, key: str) -> None:
        await self.storage.delete(self.namespace, key)

    async def all(self) -> List[T]:
        return await self.storage.all_values(self.namespace)

    async def keys(self) -> List[str]:
        return await self.storage.keys(self.namespace)

    async def temporary_set(self, key: str | int, ttl: timedelta, **kwargs) -> None:

        key = f"temporary_{key}"
        await self.storage.set(self.namespace, key, kwargs, ttl)

    async def temporary_get(self, key: str | int) -> Any:
        key = f"temporary_{key}"
        return await self.storage.get(self.namespace, key)


class CacheService:
    """
    Основной сервис кеширования. Единственная точка входа для работы с кешем.

    Содержит типизированные кеши для каждой модели через атрибуты-фасады:
    - users: CacheProtocol[User]
    - keys: CacheProtocol[Key]
    - servers: CacheProtocol[Server]
    - tariffs: CacheProtocol[Tariff]
    - gifts: CacheProtocol[GiftLink]
    - inbounds: CacheProtocol[Inbound]
    - payments: CacheProtocol[PaymentModel]
    - stocks: CacheProtocol[Stock]
    - referral_links: CacheProtocol[ReferralLink]

    ВАЖНО: ModelCache используется только внутри CacheService.
    Прямой доступ к методам ModelCache ЗАПРЕЩЕН.

    Пример использования:
        ✅ cache_service.users.get("user_id")
        ✅ cache_service.keys.set("key_id", key_obj, ttl=timedelta(hours=1))
        ✅ cache_service.servers.all()
        ❌ model_cache.get("key")  — FORBIDDEN
        ❌ ModelCache(storage, "namespace")  — FORBIDDEN

    storage (CacheStorage) используется в редких критических случаях.
    Требует документирования в комментариях.
    """

    def __init__(self, storage: CacheStorage):
        self.storage = storage
        self.users: CacheProtocol[User] = ModelCache[User](storage, "users")
        self.keys: CacheProtocol[Key] = ModelCache[Key](storage, "keys")
        self.servers: CacheProtocol[Server] = ModelCache[Server](storage, "servers")
        self.tariffs: CacheProtocol[Tariff] = ModelCache[Tariff](storage, "tariffs")
        self.gifts: CacheProtocol[GiftLink] = ModelCache[GiftLink](
            storage, "gift_links"
        )
        self.inbounds: CacheProtocol[Inbound] = ModelCache[Inbound](storage, "inbounds")
        self.payments: CacheProtocol[PaymentModel] = ModelCache[PaymentModel](
            storage, "payments"
        )
        self.stocks: CacheProtocol[Stock] = ModelCache[Stock](storage, "stocks")
        self.referral_links: CacheProtocol[ReferralLink] = ModelCache[ReferralLink](
            storage, "referral_links"
        )

    async def start(self):
        await self.storage.start()

    async def stop(self):
        await self.storage.stop()
