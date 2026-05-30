import asyncio
from typing import List, Dict, Optional, Tuple

import aiohttp
import asyncpg
from loguru import logger

from client import PanelClient
from models import Key
from services.core.data.service import ServiceDataModel


class TrafficUpdater:
    """
    Сервис для асинхронного получения и обновления данных о трафике.
    Выполняет запросы к subscription endpoint и обновляет объекты Key.
    """

    def __init__(
        self,
        model_data: ServiceDataModel,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> None:
        """
        Args:
            semaphore: Опциональный семафор для ограничения параллельных запросов
        """
        self.semaphore = semaphore or asyncio.Semaphore(30)
        self.model_data = model_data

    async def fetch_traffic_batch(
        self,
        clients: List[PanelClient],
        server_subscription_url: str,
        session: aiohttp.ClientSession,
    ) -> Dict[str, Optional[Dict]]:
        """
        Асинхронно получает данные о трафике для пакета клиентов.

        Args:
            clients: Список клиентов (PanelClient)
            server_subscription_url: Базовый URL подписки
            session: Общая aiohttp сессия

        Returns:
            Словарь: email -> данные о трафике или None
        """

        async def fetch_single(client: PanelClient) -> Tuple[str, Optional[Dict]]:
            url = (
                f"{server_subscription_url}/{client.email}"
                if client.email == client.sub_id
                else f"{server_subscription_url}/{client.sub_id}"
            )
            async with self.semaphore:
                try:
                    async with session.get(url, ssl=False) as response:
                        if response.status == 404:
                            return client.email, None
                        if response.status >= 400:
                            logger.debug(
                                "Ошибка статуса", url=url, status=response.status
                            )
                            return client.email, None

                        content_type = response.headers.get("Content-Type", "").lower()
                        if "application/json" in content_type:
                            data = await response.json()
                            return client.email, data
                        else:
                            text = await response.text()
                            return client.email, {
                                "headers": dict(response.headers),  # ← добавить заголовки
                                "content_type": content_type,
                                "status_code": response.status,
                                "text": await response.text(),
                            }
                except Exception as e:
                    logger.debug(
                        "Ошибка запроса трафика", email=client.email, error=str(e)
                    )
                    return client.email, None

        tasks = [fetch_single(client) for client in clients]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        traffic_data = {}
        for result in results:
            if isinstance(result, Exception) or not result:
                continue
            email, data = result
            traffic_data[email] = data

        logger.debug("Получены данные о трафике", count=len(traffic_data))
        return traffic_data

    async def parse_traffic_info(self, response_data: Optional[Dict]) -> Optional[Dict]:
        """
        Извлекает и парсит информацию о трафике из ответа.

        Args:
            response_data: Ответ от subscription endpoint

        Returns:
            Словарь с информацией о трафике или None
        """
        if not response_data or not isinstance(response_data, dict):
            return None

        try:
            headers = response_data.get("headers", {})
            subscription_info = headers.get("Subscription-Userinfo", "")
            if not subscription_info:
                return None

            traffic_data = {}
            for item in subscription_info.split(";"):
                item = item.strip()
                if "=" in item:
                    key, value = item.split("=", 1)
                    traffic_data[key.strip()] = value.strip()

            upload = int(traffic_data.get("upload", 0))
            download = int(traffic_data.get("download", 0))
            total = int(traffic_data.get("total", 0))
            used = upload + download

            return {
                "upload_bytes": upload,
                "download_bytes": download,
                "total_bytes": total,
                "used_bytes": used,
                "used_gb": used / (1024**3),
                "total_gb": total / (1024**3),
                "remaining_bytes": max(0, total - used),
                "usage_percent": (used / total * 100) if total > 0 else 0,
            }
        except (ValueError, TypeError, ZeroDivisionError) as e:
            logger.debug("Ошибка парсинга трафика", error=str(e))
            return None

    async def update_key_with_traffic(
        self, pool: asyncpg.Pool, key: Key, client: PanelClient, traffic_data: Optional[Dict]
    ) -> bool:
        """
        Обновляет объект Key на основе полученных данных о трафике.

        Args:
            key: Объект ключа для обновления
            client: Клиент из XUI (PanelClient) — для синхронизации expiry_time и limit_ip
            traffic_data: Данные о трафике

        Returns:
            True, если обновление прошло успешно
        """
        try:
            if not traffic_data:
                logger.debug("Нет данных о трафике для обновления", email=key.email)
                return False

            traffic_info = await self.parse_traffic_info(traffic_data)
            if not traffic_info:
                logger.debug("Не удалось извлечь информацию о трафике", email=key.email)
                return False

            # Обновляем данные ключа
            key.used_traffic = traffic_info["used_bytes"]
            key.total_gb = traffic_info["total_bytes"]
            key.expiry_time = client.expiry_time
            key.limit_ip = client.limit_ip

            await self.model_data.keys.update(pool, key, {"email": key.email})

            logger.debug(
                "Ключ обновлён трафиком",
                email=key.email,
                usage_percent=traffic_info["usage_percent"],
            )

            return True

        except Exception as e:
            logger.error(
                "Ошибка обновления ключа трафиком", email=key.email, error=str(e)
            )
            return False
