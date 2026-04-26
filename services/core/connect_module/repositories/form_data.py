import random
from typing import Dict, Any, Optional, List

import asyncpg

from config import LIST_AVAILABLE_CONNECTIONS
from logger import logger
from models import Inbound
from services.cache.key_manager import CacheKeyManager
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel


class FormConnectionData:
    """Класс получения данных для подключения к серверу"""

    def __init__(self, cache: CacheService, model_data: ServiceDataModel):
        self.cache = cache
        self.server_data = model_data.servers
        self.inbound_data = model_data.inbounds
        self._pool: Optional[asyncpg.Pool] = None

    def set_pool(self, pool: asyncpg.Pool) -> None:
        """Устанавливает пул соединений для прямого доступа к БД."""
        self._pool = pool

    async def _get_inbounds_from_db(self, server_id: int) -> List[Inbound]:
        """Получает inbounds напрямую из БД для сервера."""
        if not self._pool:
            logger.error(
                "Пул соединений не установлен для получения inbounds из БД",
                class_name=self.__class__.__name__,
                method="_get_inbounds_from_db",
                server_id=server_id
            )
            return []
        
        try:
            # Получаем все inbounds из БД и фильтруем по server_id
            all_inbounds = await self.inbound_data.service.get_all(self._pool)
            return [i for i in all_inbounds if i.server_id == server_id]
        except Exception as e:
            logger.error(
                "Ошибка при получении inbounds из БД",
                server_id=server_id,
                error=str(e),
            )
            return []

    async def _get_inbound_id(self, user_id: int, server_id: int) -> Optional[int]:
        """Получает inbound_id"""
        # CacheService.storage — прямой доступ: temporary_inbound хранит str(inbound_id), не модель
        inbound_data = await self.cache.storage.get(
            "users", CacheKeyManager.temporary_inbound(user_id)
        )
        if inbound_data:
            return int(inbound_data)
        
        # Кеш не найден — пробуем получить inbounds из БД
        inbounds = await self._get_inbounds_from_db(server_id)
        available_inbounds = [
            i.inbound_id
            for i in inbounds
            if i.inbound_id in LIST_AVAILABLE_CONNECTIONS
        ]
        
        if not available_inbounds:
            logger.error(
                "Нет доступных inbounds для сервера",
                server_id=server_id,
                user_id=user_id,
                total_inbounds=len(inbounds),
                available_count=len(available_inbounds),
                list_available_connections=LIST_AVAILABLE_CONNECTIONS,
            )
            return None
        
        return random.choice(available_inbounds)

    async def data(self, user_id: int, server_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает данные для формы"""
        server = await self.server_data.get_data(server_id)
        if not server:
            logger.error("Сервер не найден", server_id=server_id)
            return None
        
        inbound_id = await self._get_inbound_id(user_id, server_id)
        if inbound_id is None:
            logger.error(
                "Не удалось получить inbound_id",
                user_id=user_id,
                server_id=server_id,
            )
            return None
        
        subscription_url = server.subscription_url

        return {
            "api_url": server.api_url,
            "login": server.login,
            "password": server.password,
            "inbound_id": inbound_id,
            "subscription_url": subscription_url,
        }
