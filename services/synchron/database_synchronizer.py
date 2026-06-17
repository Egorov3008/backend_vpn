import asyncio
from typing import List
import aiohttp
import asyncpg
from logger import logger

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
        import time
        sync_start = time.time()
        try:
            # 1. Получаем клиентов с XUI
            fetch_start = time.time()
            clients = await self.xui_fetcher.extract_clients(xui_session)
            fetch_time = time.time() - fetch_start
            if not clients:
                logger.warning(
                    "Клиенты не найдены на панели",
                    method="extract_clients",
                    server_id=getattr(xui_session, "server_id", "unknown")
                )
                return {"total": 0, "successful": 0, "failed": 0}

            logger.info("Начало синхронизации", total_clients=len(clients), fetch_clients_time=f"{fetch_time:.2f}s")

            # 2. Сравниваем с кэшем
            compare_start = time.time()
            self.cache_comparator.set_panel_data(clients)
            await self.cache_comparator.set_cache_data(
                get_all_keys_func=self.model_data.keys.get_all,
                get_all_users_func=self.model_data.users.get_all,
            )
            out_keys, out_users, orphaned_keys = self.cache_comparator.compare()
            compare_time = time.time() - compare_start
            logger.info(
                "Сравнение с кэшем завершено",
                compare_time=f"{compare_time:.2f}s",
                missing_keys=len(out_keys),
                missing_users=len(out_users),
                orphaned_keys=len(orphaned_keys)
            )

            # 3. Восстанавливаем недостающие данные
            restore_start = time.time()
            restore_stats = await self._restore_missing_data(clients, out_keys, out_users)
            restore_time = time.time() - restore_start
            logger.info(
                "Восстановление недостающих данных завершено",
                restore_time=f"{restore_time:.2f}s",
                restored_keys=restore_stats["restored_keys"],
                restored_users=restore_stats["restored_users"]
            )

            # 3.5. Удаляем orphaned ключи (есть в кэше/БД, нет в панели)
            cleanup_start = time.time()
            cleanup_stats = await self._cleanup_orphaned_keys(orphaned_keys)
            cleanup_time = time.time() - cleanup_start
            logger.info(
                "Очистка orphaned ключей завершена",
                cleanup_time=f"{cleanup_time:.2f}s",
                deleted=cleanup_stats["deleted"]
            )

            # 3.7. Восстанавливаем tg_id на панели для ключей, где на панели tgId=0,
            # но в БД tg_id > 0. Источник истины для tg_id — наша БД.
            tg_restore_start = time.time()
            tg_restore_stats = await self._restore_tg_ids_on_panel(clients, xui_session)
            tg_restore_time = time.time() - tg_restore_start
            logger.info(
                "Восстановление tg_id на панели завершено",
                restore_time=f"{tg_restore_time:.2f}s",
                restored=tg_restore_stats["restored"],
                failed=tg_restore_stats["failed"]
            )

            # 4. Обновляем трафик пакетно
            traffic_start = time.time()
            stats = await self._update_traffic_in_batches(clients, batch_size)
            traffic_time = time.time() - traffic_start
            logger.info(
                "Обновление трафика завершено",
                traffic_time=f"{traffic_time:.2f}s",
                successful=stats["successful"],
                failed=stats["failed"]
            )

            stats["restored_keys"] = restore_stats["restored_keys"]
            stats["restored_users"] = restore_stats["restored_users"]
            stats["restored_tg_ids"] = tg_restore_stats["restored"]
            stats["panel_clients"] = len(clients)
            stats["missing_keys"] = len(out_keys)
            stats["missing_users"] = len(out_users)
            stats["orphaned_keys"] = len(orphaned_keys)
            stats["deleted_orphaned"] = cleanup_stats["deleted"]

            # Поля, которые ожидает бот в /admin/sync отчёте.
            # Алиасы + недостающие счётчики (db_keys_before/after, synced).
            # Считаем ПОСЛЕ _cleanup_orphaned_keys, чтобы cache отражал
            # итоговое состояние БД после удаления orphaned.
            stats["db_keys_before"] = len(self.cache_comparator.keys_cache)
            stats["db_keys_after"] = await self.model_data.keys.count()
            stats["synced"] = (
                len(self.cache_comparator.keys_cache) - len(orphaned_keys)
            )
            stats["created"] = restore_stats["restored_keys"]
            stats["panel_updated"] = tg_restore_stats["restored"]
            stats["db_updated"] = stats["successful"]
            stats["orphaned_deleted"] = cleanup_stats["deleted"]
            stats["traffic_updated"] = stats["successful"]
            stats["traffic_failed"] = stats["failed"]

            total_time = time.time() - sync_start
            logger.info(
                "Синхронизация завершена (полная статистика)",
                total_time=f"{total_time:.2f}s",
                fetch_clients_time=f"{fetch_time:.2f}s",
                compare_time=f"{compare_time:.2f}s",
                restore_time=f"{restore_time:.2f}s",
                cleanup_time=f"{cleanup_time:.2f}s",
                tg_restore_time=f"{tg_restore_time:.2f}s",
                traffic_time=f"{traffic_time:.2f}s",
                **stats
            )
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

    async def _restore_tg_ids_on_panel(
        self, clients: List[PanelClient], xui_session: "XUISession"
    ) -> dict:
        """
        Восстанавливает tgId на панели для ключей, где на панели tgId=0,
        но в БД tg_id > 0.

        Источник истины для tg_id — наша БД. Это защищает от случая, когда
        на панели tgId был обнулён (миграция/правка/баг) — синхронизатор
        вернёт правильную привязку к пользователю.

        Сценарий-баг: пользователь 397349989, email=6cx7ah — на панели tgId=0,
        в БД tg_id=397349989. Без этого шага ключ был бы помечен orphaned
        (когда в БД он есть) и удалён. С этим шагом tgId восстанавливается
        на панели.
        """
        restored = 0
        failed = 0
        for client in clients:
            if not client.email or client.tg_id != 0:
                continue
            try:
                db_key = await self.model_data.keys.get_data(client.email)
                if not db_key or not getattr(db_key, "tg_id", None):
                    continue
                if db_key.tg_id <= 0:
                    continue
                await xui_session.update_standalone_client(
                    client.email, tgId=db_key.tg_id
                )
                restored += 1
                logger.info(
                    "Восстановлен tgId на панели",
                    email=client.email,
                    tg_id=db_key.tg_id,
                )
            except Exception as e:
                failed += 1
                logger.error(
                    "Ошибка восстановления tgId на панели",
                    email=client.email,
                    error=str(e),
                )
        return {"restored": restored, "failed": failed}

    async def _update_traffic_in_batches(
        self, clients: List[PanelClient], batch_size: int
    ) -> dict:
        """Обновляет трафик пакетно"""
        import time
        successful = 0
        failed = 0

        # ✅ Использование CacheService через ServiceDataModel
        server_start = time.time()
        server = await self.model_data.servers.get_data(settings.xui_server_id)
        if not server:
            from models.servers.server import get_env_server
            server = get_env_server()
            logger.debug(
                "Используем сервер из .env для синхронизации",
                server_id=settings.xui_server_id,
                method="update_traffic_batch"
            )
        server_lookup_time = time.time() - server_start
        if not server:
            logger.error(
                "Сервер не найден",
                server_id=settings.xui_server_id,
                method="update_traffic_batch"
            )
            return {"total": 0, "successful": 0, "failed": 1}

        session_start = time.time()
        session = await self.get_client_session()
        session_init_time = time.time() - session_start

        logger.info(
            "Начало обновления трафика",
            total_clients=len(clients),
            batch_size=batch_size,
            server_lookup_time=f"{server_lookup_time:.2f}s",
            session_init_time=f"{session_init_time:.2f}s"
        )

        batch_times = []
        for i in range(0, len(clients), batch_size):
            batch_start = time.time()
            batch = clients[i : i + batch_size]
            sub_url = settings.xui_subscription_url or server.subscription_url
            traffic_data = await self.traffic_updater.fetch_traffic_batch(
                batch, sub_url, session
            )

            batch_successful = 0
            batch_failed = 0
            for client in batch:
                key = await self.model_data.keys.get_data(client.email, self.pool)
                if not key:
                    logger.warning("Ключ не найден в кэше/БД", email=client.email)
                    failed += 1
                    batch_failed += 1
                    continue

                updated = await self.traffic_updater.update_key_with_traffic(
                    self.pool, key, client, traffic_data.get(client.email)
                )
                if updated:
                    successful += 1
                    batch_successful += 1
                else:
                    failed += 1
                    batch_failed += 1

            batch_time = time.time() - batch_start
            batch_times.append(batch_time)
            logger.debug(
                "Пакет обработан",
                batch_num=i // batch_size + 1,
                batch_size=len(batch),
                successful=batch_successful,
                failed=batch_failed,
                batch_time=f"{batch_time:.2f}s"
            )

            # Небольшая пауза между пакетами
            if i + batch_size < len(clients):
                await asyncio.sleep(0.1)

        avg_batch_time = sum(batch_times) / len(batch_times) if batch_times else 0
        max_batch_time = max(batch_times) if batch_times else 0
        logger.info(
            "Обновление трафика завершено",
            successful=successful,
            failed=failed,
            total_batches=len(batch_times),
            avg_batch_time=f"{avg_batch_time:.2f}s",
            max_batch_time=f"{max_batch_time:.2f}s"
        )
        return {
            "total": successful + failed,
            "successful": successful,
            "failed": failed,
        }
