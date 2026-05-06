from typing import TypeVar, Generic, Optional, List, Any, Dict

import asyncpg

from database import BaseRepository
from logger import logger
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService

T = TypeVar("T")


class BaseData(Generic[T]):
    """Отвечает за чтение и сохранение данных с единообразными ключами кеша.

    ✅ Использует CacheService согласно Cache Access Rules.
    """

    def __init__(
        self, model: T, cache_service: CacheService, service_data: BaseRepository
    ):
        self.model = model
        self.cache_service = cache_service
        self.service = service_data
        self.key_manager = CacheKeyManager()
        # Определяем метод генерации ключа по типу модели
        self.model_name = self.model.__name__.lower()

    def _get_cache_model(self) -> Any:
        """Получить ModelCache через CacheService по типу модели.

        ✅ Единственный способ доступа к ModelCache (через CacheService).
        """
        if self.model_name == "user":
            return self.cache_service.users
        elif self.model_name == "key":
            return self.cache_service.keys
        elif self.model_name == "server":
            return self.cache_service.servers
        elif self.model_name == "tariff":
            return self.cache_service.tariffs
        elif self.model_name == "giftlink":
            return self.cache_service.gifts
        elif self.model_name == "inbound":
            return self.cache_service.inbounds
        elif self.model_name == "paymentmodel":
            return self.cache_service.payments
        elif self.model_name == "stock":
            return self.cache_service.stocks
        elif self.model_name == "referrallink":
            return self.cache_service.referral_links
        else:
            raise ValueError(f"Unknown model type: {self.model_name}")

    async def get_data(self, identifier: int | str) -> Optional[T]:
        """Получить объект из кеша по ID"""
        try:
            if not identifier:
                raise ValueError(f"{self.__class__.__name__}: identifier is required")
            # Генерируем ключ через CacheKeyManager для единообразия
            cache_key = self._generate_key(identifier)
            obj = await self._get_cache_model().get(cache_key)
            if not obj:
                raise ValueError(
                    f"{self.__class__.__name__}: объект с id={identifier} не найден"
                )
            return obj
        except Exception as e:
            self._log_error(e, identifier)
            return None

    async def get_all(self) -> List[T]:
        """Получить все объекты из кеша"""
        return await self._get_cache_model().all()

    async def exists(self, identifier: int) -> bool:
        """Проверить существует ли объект в кеше"""
        return await self.get_data(identifier) is not None

    async def count(self) -> int:
        """Количество объектов в кеше"""
        return len(await self.get_all())

    def _log_error(self, e: Exception, identifier: int | str):
        """Логирование ошибок доступа к кешу"""
        try:
            error_msg = str(e)
            # Если объект не найден в кеше — это не ошибка, а нормальная ситуация
            if "не найден" in error_msg:
                logger.warning(
                    "Объект не найден в кеше (cache miss)",
                    class_name=self.__class__.__name__,
                    identifier=identifier,
                )
            else:
                logger.error(
                    "Ошибка при получении объекта из кеша",
                    exc_info=True,
                    error_msg=error_msg,
                    class_name=self.__class__.__name__,
                    identifier=identifier,
                )
        except:
            pass

    def _extract_identifier(self, data: T) -> int | str | tuple:
        """Извлечь идентификатор из объекта в зависимости от типа"""
        if self.model_name == "user":
            return data.tg_id
        elif self.model_name == "key":
            return data.email
        elif self.model_name == "server":
            return data.id
        elif self.model_name == "tariff":
            return data.id
        elif self.model_name == "giftlink":
            return data.sender_tg_id
        elif self.model_name == "inbound":
            return (data.server_id, data.inbound_id)
        elif self.model_name == "paymentmodel":
            return data.payment_id
        elif self.model_name == "stock":
            return data.tg_id
        elif self.model_name == "referrallink":
            return data.token
        else:
            # Fallback: попытаться использовать id если существует
            return getattr(data, "id", str(data))

    def _generate_key(self, identifier: int | str | tuple) -> str:
        """Генерировать ключ кеша через CacheKeyManager"""
        if self.model_name == "user":
            return self.key_manager.user(identifier)
        elif self.model_name == "key":
            return self.key_manager.key(identifier)
        elif self.model_name == "server":
            return self.key_manager.server(identifier)
        elif self.model_name == "tariff":
            return self.key_manager.tariff(identifier)
        elif self.model_name == "giftlink":
            return self.key_manager.gift(identifier)
        elif self.model_name == "inbound":
            # Inbound требует (server_id, inbound_id)
            if isinstance(identifier, tuple) and len(identifier) == 2:
                return self.key_manager.inbound(identifier[0], identifier[1])
            else:
                raise ValueError(
                    f"Inbound требует кортеж (server_id, inbound_id), получен: {identifier}"
                )
        elif self.model_name == "paymentmodel":
            return self.key_manager.payment(identifier)
        elif self.model_name == "stock":
            return self.key_manager.stock(identifier)
        elif self.model_name == "referrallink":
            return self.key_manager.referral_link(identifier)
        else:
            # Fallback для неизвестных типов (формат entity_{id})
            return f"{self.model_name}_{identifier}"

    async def save_data(self, conn: asyncpg.Pool, data: T, **kwargs) -> None:
        """Сохранить данные в БД и в кеш"""
        key, value = next(iter(kwargs.items()))
        if not getattr(data, key):
            raise ValueError(f"{self.__class__.__name__}: {key} is required")

        from logger import logger
        logger.debug(f"Сохранение {self.model_name} в БД", identifier=value, data_dict=data.to_dict())

        try:
            await self.service.create(conn, **data.to_dict())
            logger.debug(f"{self.model_name} успешно сохранен в БД", identifier=value)
        except Exception as e:
            logger.error(f"Ошибка при сохранении {self.model_name} в БД", identifier=value, error=str(e))
            raise

        # Используем CacheKeyManager для единообразного ключа
        cache_key = self._generate_key(value)
        await self._get_cache_model().set(cache_key, data)
        logger.debug(f"{self.model_name} сохранен в кеше", cache_key=cache_key)

    async def delete_data(self, conn: asyncpg.Pool, data: T) -> None:
        """Удалить данные из БД и кеша"""
        await self.service.delete(conn, **data.to_dict())
        # Используем CacheKeyManager для единообразного ключа
        identifier = self._extract_identifier(data)
        cache_key = self._generate_key(identifier)
        await self._get_cache_model().delete(cache_key)

    async def get_by(self, **kwargs) -> List[Optional[T]] | Optional[T]:
        """Возвращает список объектов по ключевым полям."""
        data = await self.get_all()
        items = list(iter(kwargs.items()))
        if not items:
            return []
        key, value = items[0]
        if not data:
            return []
        values = []
        for d in data:
            if getattr(d, key) == value:
                values.append(d)

        return values if len(values) > 1 else (values[0] if values else None)

    async def update(
        self, conn: asyncpg.Pool, data: T, search_data: Dict[str, Any]
    ) -> T:
        """Обновить данные в БД и кеше"""
        key, value = next(iter(search_data.items()))
        if not getattr(data, key):
            raise ValueError(f"{self.__class__.__name__}: {key} is required")

        await self.service.update(conn, search_data, **data.to_dict())
        # Используем CacheKeyManager для единообразного ключа
        cache_key = self._generate_key(value)
        await self._get_cache_model().set(cache_key, data)
        return data
