import random
from typing import Dict, Any, Optional, List

import asyncpg

from config import LIST_AVAILABLE_CONNECTIONS, settings
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
        self._xui = None

    def set_pool(self, pool: asyncpg.Pool) -> None:
        """Устанавливает пул соединений для прямого доступа к БД."""
        self._pool = pool

    def set_xui_session(self, xui) -> None:
        """Устанавливает XUISession для получения inbounds из панели."""
        self._xui = xui

    async def _get_inbounds_from_db(self, server_id: int) -> List[Inbound]:
        """Получает inbounds напрямую из БД для сервера (legacy fallback)."""
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

    async def _get_available_inbound_ids_from_panel(self) -> List[int]:
        """Получает список inbound IDs из 3x-UI панели и фильтрует по .env"""
        if not self._xui:
            logger.error(
                "XUISession не установлен для получения inbounds из панели",
                class_name=self.__class__.__name__,
                method="_get_available_inbound_ids_from_panel",
            )
            return []

        try:
            inbounds = await self._xui.get_inbounds()
        except Exception as e:
            logger.error(
                "Ошибка при получении inbounds из панели",
                error=str(e),
            )
            return []

        available = []
        for ib in inbounds:
            # 3x-ui API возвращает inbound с полем 'id'
            ib_id = ib.get("id")
            if ib_id is not None and ib_id in LIST_AVAILABLE_CONNECTIONS:
                available.append(ib_id)

        logger.debug(
            "Доступные inbounds из панели",
            total_inbounds=len(inbounds),
            available_count=len(available),
            list_available_connections=LIST_AVAILABLE_CONNECTIONS,
        )
        return available

    async def _get_inbound_ids(self, user_id: int, server_id: int) -> List[int]:
        """Возвращает список inbound_ids для привязки клиента."""
        # 1. Пробуем получить из панели (primary source)
        panel_inbounds = await self._get_available_inbound_ids_from_panel()
        if panel_inbounds:
            return panel_inbounds

        # 2. Fallback: legacy DB
        inbounds = await self._get_inbounds_from_db(server_id)
        available_inbounds = [
            i.inbound_id
            for i in inbounds
            if i.inbound_id in LIST_AVAILABLE_CONNECTIONS
        ]

        if available_inbounds:
            return available_inbounds

        # 3. Hard fallback: direct .env list
        if LIST_AVAILABLE_CONNECTIONS:
            logger.warning(
                "Используем LIST_AVAILABLE_CONNECTIONS из .env напрямую",
                list_available_connections=LIST_AVAILABLE_CONNECTIONS,
            )
            return LIST_AVAILABLE_CONNECTIONS

        logger.error(
            "Нет доступных inbounds",
            user_id=user_id,
            server_id=server_id,
        )
        return []

    async def data(self, user_id: int, server_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает данные для формы"""
        server = await self.server_data.get_data(server_id)
        if not server:
            # Fallback: single-panel mode — credentials from .env
            from models.servers.server import get_env_server
            server = get_env_server()
            logger.debug("Используем сервер из .env", server_id=server_id)

        if not server:
            logger.error("Сервер не найден", server_id=server_id)
            return None

        inbound_ids = await self._get_inbound_ids(user_id, server_id)
        if not inbound_ids:
            logger.error(
                "Не удалось получить inbound_ids",
                user_id=user_id,
                server_id=server_id,
            )
            return None

        subscription_url = settings.xui_subscription_url or server.subscription_url

        return {
            "api_url": server.api_url,
            "login": server.login,
            "password": server.password,
            "inbound_ids": inbound_ids,
            "subscription_url": subscription_url,
        }
