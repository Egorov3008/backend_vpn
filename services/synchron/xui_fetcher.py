import json
from typing import Dict, List

from logger import logger

from client import XUISession, PanelClient


async def _fetch_id_tgid_map(xui_session: XUISession) -> Dict[str, dict]:
    """Строит email -> {"id", "tg_id"} по данным всех инбаундов.

    Пагинированный список клиентов (``list_clients_all``, standalone API
    /clients/list) НЕ содержит полей id/tgId в ответе панели — проверено на
    живых данных: там есть только email/traffic/expiry. Единственное место,
    где эти поля реально есть — полный дамп инбаундов (get_inbounds),
    settings.clients[]. Без этого обогащения _to_panel_client всегда получал
    id="" и tg_id=0 для каждого клиента, из-за чего синхронизатор считал
    ВСЕХ клиентов "потерявшими tgId" и пересоздавал эту привязку заново на
    каждом прогоне синка, даже если на панели всё уже было корректно.
    """
    try:
        inbounds = await xui_session.get_inbounds()
    except Exception as e:
        logger.error("Ошибка получения инбаундов для обогащения id/tgId", error=str(e))
        return {}

    id_tgid_map: Dict[str, dict] = {}
    for inbound in inbounds:
        settings = inbound.get("settings")
        if isinstance(settings, str):
            try:
                settings = json.loads(settings)
            except (ValueError, TypeError):
                continue
        if not isinstance(settings, dict):
            continue
        for raw_client in settings.get("clients", []) or []:
            email = raw_client.get("email")
            if not email or email in id_tgid_map:
                continue
            id_tgid_map[email] = {
                "id": str(raw_client.get("id", "")),
                "tg_id": raw_client.get("tgId") or raw_client.get("tg_id") or 0,
            }
    return id_tgid_map


def _to_panel_client(raw: dict) -> PanelClient:
    """Конвертирует raw dict из standalone API в PanelClient."""
    return PanelClient(
        id=str(raw.get("id", "")),
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
            raw_list = await xui_session.list_clients_all()
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

        # Обогащаем id/tgId из инбаундов — list_clients_all их не отдаёт.
        id_tgid_map = await _fetch_id_tgid_map(xui_session)
        enriched = 0
        not_found_in_inbounds = 0
        for client in all_clients:
            enrich = id_tgid_map.get(client.email)
            if enrich:
                client.id = enrich["id"]
                client.tg_id = enrich["tg_id"]
                enriched += 1
            else:
                not_found_in_inbounds += 1
        logger.info(
            "Клиенты обогащены id/tgId из инбаундов",
            enriched=enriched,
            not_found_in_inbounds=not_found_in_inbounds,
            inbound_clients_total=len(id_tgid_map),
        )

        # Фильтруем только валидных клиентов.
        # ВАЖНО: tg_id может быть 0 (например, когда на панели клиент потерял
        # привязку к пользователю) — такие клиенты НЕЛЬЗЯ отбрасывать,
        # иначе синхронизатор посчитает их удалёнными с панели и сотрёт
        # из БД как orphaned (см. баг с пользователем 397349989 / email 6cx7ah).
        valid_clients = [
            client
            for client in all_clients
            if client.email and isinstance(client.tg_id, int)
        ]

        logger.info(
            "Клиенты извлечены и отфильтрованы",
            total_extracted=len(all_clients),
            valid=len(valid_clients),
        )
        return valid_clients
