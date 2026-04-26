from typing import List
import py3xui
from logger import logger
from py3xui.client import Client

from client import XUISession


class XUIFetcher:
    """
    Сервис для получения данных с XUI-панели.
    Не хранит состояние — работает с переданной сессией.
    Отвечает за:
      - Получение списка инбаундов
      - Извлечение и валидацию клиентов
    """

    async def fetch_inbounds(self, xui_session: XUISession) -> List[py3xui.Inbound]:
        """
        Получает список всех инбаундов с XUI-панели.

        Args:
            xui_session: Активная сессия XUI (внедряется через middleware)

        Returns:
            Список инбаундов или пустой список при ошибке
        """
        try:
            inbounds: List[py3xui.Inbound] = await xui_session.get_inbounds()
            if not inbounds:
                logger.warning(
                    "Список инбаундов пуст",
                    server_id=getattr(xui_session, "server_id", "unknown")
                )
                return []
            logger.info("Получены инбаунды с XUI", count=len(inbounds))
            return inbounds
        except Exception as e:
            logger.error(
                "Ошибка получения инбаундов с XUI",
                server_id=getattr(xui_session, "server_id", "unknown"),
                error=str(e)
            )
            return []

    async def extract_clients(self, xui_session: XUISession) -> List[Client]:
        """
        Полностью извлекает валидных клиентов из всех инбаундов.

        Args:
            xui_session: Активная сессия XUI

        Returns:
            Список корректных клиентов с email и tg_id
        """
        inbounds = await self.fetch_inbounds(xui_session)
        if not inbounds:
            return []

        all_clients: List[Client] = []
        for inbound in inbounds:
            try:
                for client in inbound.settings.clients:
                    if not hasattr(client, "inbound_id") or client.inbound_id is None:
                        client.inbound_id = inbound.id
                    all_clients.append(client)
            except AttributeError as e:
                logger.debug(
                    "Ошибка доступа к клиентам инбаунда",
                    inbound_id=inbound.id,
                    error=str(e),
                )
                continue

        # Фильтруем только валидных клиентов
        valid_clients = [
            client
            for client in all_clients
            if client
            and hasattr(client, "email")
            and client.email
            and hasattr(client, "tg_id")
            and isinstance(client.tg_id, int)
            and client.tg_id > 0
        ]

        logger.info(
            "Клиенты извлечены и отфильтрованы",
            total_extracted=len(all_clients),
            valid=len(valid_clients),
        )
        return valid_clients
