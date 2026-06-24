from typing import Dict, Any, Optional, List

from config import LIST_AVAILABLE_CONNECTIONS, settings
from logger import logger
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel


class FormConnectionData:
    """Класс получения данных для подключения к серверу"""

    def __init__(self, cache: CacheService, model_data: ServiceDataModel):
        self.cache = cache
        self.server_data = model_data.servers
        self._xui = None

    def set_xui_session(self, xui) -> None:
        """Устанавливает XUISession для получения inbounds из панели."""
        self._xui = xui

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

        # 2. Fallback: direct .env list
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
