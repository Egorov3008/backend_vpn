import asyncio
from typing import List
import aiohttp
import asyncpg
from loguru import logger

from client import XUISession, PanelClient
from config import settings
from services.synchron.xui_fetcher import XUIFetcher
from services.synchron.cache_comparator import CacheComparator
from services.synchron.key_creator import KeyCreator
from services.synchron.traffic import TrafficUpdater
from services.core.data.service import ServiceDataModel


class DatabaseSynchronizer:
    """
    Основной оркестратор синхронизации данных.
    Использует специализированные сервисы для:
    - Получения данных с XUI
    - Сравнения с кэшем
    - Создания новых ключей
    - Обновления трафика
    - Сохранения в БД и кэш
    """

    def __init__(
        self,
        xui_fetcher: XUIFetcher,
        cache_comparator: CacheComparator,
        key_creator: KeyCreator,
        traffic_updater: TrafficUpdater,
        model_data: ServiceDataModel,
        pool: asyncpg.Pool,
    ) -> None:
        self.xui_fetcher = xui_fetcher
        self.cache_comparator = cache_comparator
        self.key_creator = key_creator
        self.traffic_updater = traffic_updater
        self.model_data = model_data
        self.pool = pool
        self._session: aiohttp.ClientSession | None = None

    async def get_client_session(self) -> aiohttp.ClientSession:
        """Ленивая инициализация aiohttp-сессии"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=100, limit_per_host=20, keepalive_timeout=30
            )
            timeout = aiohttp.ClientTimeout(total=60, connect=10, sock_read=30)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    async def close(self) -> None:
        """Закрытие ресурсов"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        await self.close()

    async def sync_data(self, xui_session: XUISession, batch_size: int = 50) -> dict:
        """
        Выполняет полный цикл синхронизации.

        Args:
            xui_session: Активная сессия XUI
            batch_size: Размер пакета для обработки

        Returns:
            Статистика синхронизации
        """
        try:
            # 1. Получаем клиентов с XUI
            clients = await self.xui_fetcher.extract_clients(xui_session)
            if not clients:
                logger.warning(
                    "Клиенты не найдены на панели",
                    method="extract_clients",
                    server_id=getattr(xui_session, "server_id", "unknown")
                )
                return {"total": 0, "successful": 0, "failed": 0}

            logger.info("Начало синхронизации", total_clients=len(clients))

            # 2. Сравниваем с кэшем
            self.cache_comparator.set_panel_data(clients)
            await self.cache_comparator.set_cache_data(
                get_all_keys_func=self.model_data.keys.get_all,
                get_all_users_func=self.model_data.users.get_all,
            )
            out_keys, out_users, orphaned_keys = self.cache_comparator.compare()

            # 3. Восстанавливаем недостающие данные
            restore_stats = await self._restore_missing_data(clients, out_keys, out_users)

            # 3.5. Удаляем orphaned ключи (есть в кэше/БД, нет в панели)
            cleanup_stats = await self._cleanup_orphaned_keys(orphaned_keys)

            # 4. Обновляем трафик пакетно
            stats = await self._update_traffic_in_batches(clients, batch_size)
            stats["restored_keys"] = restore_stats["restored_keys"]
            stats["restored_users"] = restore_stats["restored_users"]
            stats["panel_clients"] = len(clients)
            stats["missing_keys"] = len(out_keys)
            stats["missing_users"] = len(out_users)
            stats["orphaned_keys"] = len(orphaned_keys)
            stats["deleted_orphaned"] = cleanup_stats["deleted"]
            return stats

        except Exception as e:
            logger.error("Критическая ошибка синхронизации", error=str(e))
            return {"total": 0, "successful": 0, "failed": 0, "error": str(e)}

    async def _restore_missing_data(
        self, clients: List[PanelClient], out_keys: List[str], out_users: List[int]
    ) -> dict:
        """Восстанавливает отсутствующие ключи и пользователей"""
        key_map = {c.email: c for c in clients}
        restored_keys = 0
        restored_users = 0

        for email in out_keys:
            client = key_map.get(email)
            if not client:
                continue

            if client.tg_id in out_users:
                await self.key_creator.ensure_user_exists(client.tg_id)
                restored_users += 1

            result = await self.key_creator.create_key(client)
            if result:
                restored_keys += 1
                logger.info(
                    "Восстановлен отсутствующий ключ",
                    email=email,
                    tg_id=client.tg_id,
                )

        return {"restored_keys": restored_keys, "restored_users": restored_users}

    async def _cleanup_orphaned_keys(self, orphaned_keys: List[str]) -> dict:
        """Удаляет ключи из БД и кэша, которых больше нет в панели."""
        deleted = 0
        for email in orphaned_keys:
            try:
                key = await self.model_data.keys.get_data(email)
                if key:
                    await self.model_data.data_service.keys.delete(self.pool, email=email)
                    await self.model_data.cache_service.keys.delete(f"key_{email}")
                    deleted += 1
                    logger.info("Удалён orphaned ключ", email=email)
            except Exception as e:
                logger.error("Ошибка удаления orphaned ключа", email=email, error=str(e))
        return {"deleted": deleted}

    async def _update_traffic_in_batches(
        self, clients: List[PanelClient], batch_size: int
    ) -> dict:
        """Обновляет трафик пакетно"""
        successful = 0
        failed = 0

        # ✅ Использование CacheService через ServiceDataModel
        server = await self.model_data.servers.get_data(settings.xui_server_id)
        if not server:
            from models.servers.server import get_env_server
            server = get_env_server()
            logger.debug(
                "Используем сервер из .env для синхронизации",
                server_id=settings.xui_server_id,
                method="update_traffic_batch"
            )
        if not server:
            logger.error(
                "Сервер не найден",
                server_id=settings.xui_server_id,
                method="update_traffic_batch"
            )
            return {"total": 0, "successful": 0, "failed": 1}

        session = await self.get_client_session()

        for i in range(0, len(clients), batch_size):
            batch = clients[i : i + batch_size]
            sub_url = settings.xui_subscription_url or server.subscription_url
            traffic_data = await self.traffic_updater.fetch_traffic_batch(
                batch, sub_url, session
            )

            for client in batch:
                key = await self.model_data.keys.get_data(client.email, self.pool)
                if not key:
                    logger.warning("Ключ не найден в кэше/БД", email=client.email)
                    failed += 1
                    continue

                updated = await self.traffic_updater.update_key_with_traffic(
                    self.pool, key, client, traffic_data.get(client.email)
                )
                if updated:
                    successful += 1
                else:
                    failed += 1

            # Небольшая пауза между пакетами
            if i + batch_size < len(clients):
                await asyncio.sleep(0.1)

        logger.info(
            "Обновление трафика завершено", successful=successful, failed=failed
        )
        return {
            "total": successful + failed,
            "successful": successful,
            "failed": failed,
        }
