from typing import List

from logger import logger

from client import XUISession, PanelClient


def _to_panel_client(raw: dict) -> PanelClient:
    """Конвертирует raw dict из standalone API в PanelClient."""
    return PanelClient(
        id=raw.get("id", ""),
        email=raw.get("email", ""),
        tg_id=raw.get("tgId") or raw.get("tg_id") or 0,
        limit_ip=raw.get("limitIp") or raw.get("limit_ip") or 0,
        total_gb=raw.get("totalGB") or raw.get("total_gb") or 0,
        expiry_time=raw.get("expiryTime") or raw.get("expiry_time") or 0,
        inbound_id=(raw.get("inboundIds") or [0])[0] if raw.get("inboundIds") else raw.get("inbound_id", 0),
        inbound_ids=list(raw.get("inboundIds", []) or []),
        sub_id=raw.get("subId") or raw.get("sub_id", ""),
        enable=raw.get("enable", True),
        flow=raw.get("flow", ""),
        group=raw.get("group", ""),
        comment=raw.get("comment", ""),
    )


class XUIFetcher:
    """
    Сервис для получения данных с XUI-панели.
    Не хранит состояние — работает с переданной сессией.
    Отвечает за:
      - Получение списка инбаундов (legacy, через py3xui)
      - Извлечение и валидацию клиентов (standalone API v3.2.0)
    """

    async def fetch_inbounds(self, xui_session: XUISession) -> list:
        """
        Получает список всех инбаундов с XUI-панели.

        Args:
            xui_session: Активная сессия XUI (внедряется через middleware)

        Returns:
            Список инбаундов или пустой список при ошибке
        """
        try:
            inbounds = await xui_session.get_inbounds()
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

    async def extract_clients(self, xui_session: XUISession) -> List[PanelClient]:
        """
        Получает список всех standalone-клиентов через v3.2.0 API.

        Args:
            xui_session: Активная сессия XUI

        Returns:
            Список корректных PanelClient с email и tg_id
        """
        try:
            raw_list = await xui_session.list_clients()
        except Exception as e:
            logger.error(
                "Ошибка получения списка клиентов через standalone API",
                server_id=getattr(xui_session, "server_id", "unknown"),
                error=str(e)
            )
            return []

        if not raw_list:
            logger.warning(
                "Список standalone клиентов пуст",
                server_id=getattr(xui_session, "server_id", "unknown")
            )
            return []

        all_clients: List[PanelClient] = []
        for raw in raw_list:
            try:
                if isinstance(raw, dict):
                    all_clients.append(_to_panel_client(raw))
            except Exception as e:
                logger.debug(
                    "Ошибка парсинга клиента",
                    raw=raw,
                    error=str(e),
                )
                continue

        # Фильтруем только валидных клиентов
        valid_clients = [
            client
            for client in all_clients
            if client.email and isinstance(client.tg_id, int) and client.tg_id > 0
        ]

        logger.info(
            "Клиенты извлечены и отфильтрованы",
            total_extracted=len(all_clients),
            valid=len(valid_clients),
        )
        return valid_clients
