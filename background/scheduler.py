"""APScheduler setup for backend background tasks."""

import asyncio
import asyncpg
import time
from typing import Optional, Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from logger import logger
from services.core.data.service import ServiceDataModel


class SyncScheduler:
    """
    Инкапсулирует состояние синхронизации и предотвращает race conditions.

    Атомарная проверка и установка флага _sync_in_progress внутри lock
    гарантирует, что только одна синхронизация выполняется одновременно.
    """

    def __init__(self, service_data: ServiceDataModel, pool: asyncpg.Pool):
        self._service_data = service_data
        self._pool = pool
        self._sync_lock = asyncio.Lock()
        self._sync_in_progress = False
        self._last_sync_time: Optional[float] = None
        self._sync_count = 0

    @property
    def is_sync_in_progress(self) -> bool:
        """Thread-safe доступ к флагу синхронизации."""
        return self._sync_in_progress

    @property
    def stats(self) -> Dict[str, Any]:
        """Статистика синхронизаций."""
        return {
            "is_sync_in_progress": self._sync_in_progress,
            "last_sync_time": self._last_sync_time,
            "sync_count": self._sync_count,
        }

    async def sync_cache(self) -> Dict[str, Any]:
        """
        Синхронизирует кэш с БД и панелью.

        Race condition fix: проверка _sync_in_progress внутри lock,
        а не до него. Это гарантирует атомарность проверки и установки.

        Returns:
            Dict со статусом синхронизации
        """
        # Атомарная проверка и захват блокировки
        async with self._sync_lock:
            if self._sync_in_progress:
                logger.warning("Синхронизация уже запущена — пропуск дублирующегося запуска")
                return {"status": "skipped", "reason": "sync_already_in_progress"}

            self._sync_in_progress = True
            self._sync_count += 1

        # Выполняем синхронизацию вне lock (но флаг остается True)
        sync_start = time.time()
        try:
            from database.service import DataService
            from services.cache.loader import LoadingService

            data_service = DataService()
            loader = LoadingService(
                cache=self._service_data.cache_service,
                data_service=data_service,
                pool=self._pool,
            )
            cache_load_start = time.time()
            await loader.loading()
            cache_load_time = time.time() - cache_load_start
            logger.info("Синхронизация кеша завершена", cache_load_time=f"{cache_load_time:.2f}s")

            # Запускаем синхронизацию с панелью после загрузки кэша
            logger.info("Запуск синхронизации с панелью (отложенный)")
            panel_sync_result = await self._sync_panel()
            total_time = time.time() - sync_start
            self._last_sync_time = total_time

            logger.info(
                "Полная синхронизация (кэш + панель) завершена",
                total_time=f"{total_time:.2f}s",
                cache_load_time=f"{cache_load_time:.2f}s",
                panel_sync_time=panel_sync_result.get("sync_time", "N/A")
            )

            return {"status": "success", "message": "Cache synchronized"}
        except Exception as e:
            logger.error("Ошибка синхронизации кеша", error=str(e), exc_info=True)
            return {"status": "error", "error": str(e)}
        finally:
            # Сбрасываем флаг внутри lock для атомарности
            async with self._sync_lock:
                self._sync_in_progress = False

    async def _sync_panel(self) -> Dict[str, Any]:
        """Sync panel clients with DB+cache and clean up orphaned keys."""
        from client import XUISession
        from services.cache.loader import LoadingService
        from services.synchron.database_synchronizer import DatabaseSynchronizer
        from services.synchron.xui_fetcher import XUIFetcher
        from services.synchron.cache_comparator import CacheComparator
        from services.synchron.key_creator import KeyCreator
        from services.synchron.tariff_matcher import TariffMatcher
        from services.synchron.traffic import TrafficUpdater
        from database.service import DataService
        import time

        sync_start = time.time()
        logger.info("Начало синхронизации с панелью")
        try:
            data_service = DataService()
            loader = LoadingService(
                cache=self._service_data.cache_service,
                data_service=data_service,
                pool=self._pool,
            )
            session_init_start = time.time()
            xui = XUISession(model_service=self._service_data, loading=loader)
            session_init_time = time.time() - session_init_start

            synchronizer = DatabaseSynchronizer(
                xui_fetcher=XUIFetcher(),
                cache_comparator=CacheComparator(),
                key_creator=KeyCreator(self._service_data, self._pool, TariffMatcher(self._service_data)),
                traffic_updater=TrafficUpdater(self._service_data),
                model_data=self._service_data,
                pool=self._pool,
            )
            async with synchronizer:
                stats = await synchronizer.sync_data(xui_session=xui)
                total_time = time.time() - sync_start
                stats["sync_time"] = f"{total_time:.2f}s"
                logger.info(
                    "Синхронизация с панелью завершена",
                    session_init_time=f"{session_init_time:.2f}s",
                    total_time=f"{total_time:.2f}s",
                    **stats
                )
                return {"status": "success", "sync_time": f"{total_time:.2f}s", **stats}
        except Exception as e:
            logger.error("Ошибка синхронизации с панелью", error=str(e), exc_info=True)
            return {"status": "error", "error": str(e)}

    async def run_notifications(self) -> None:
        """Run notification funnels cycle."""
        from services.notifications.manager import FunnelManager
        from services.notifications.funnels.key_expiry_24h import KeyExpiryFunnel24h
        from services.notifications.funnels.key_expiry_10h import KeyExpiryFunnel10h
        from services.notifications.funnels.key_expired_grace import KeyExpiredGraceFunnel
        from services.notifications.funnels.trial_reminder import TrialReminderFunnel
        from services.notifications.funnels.cold_lead import ColdLeadFunnel
        from services.notifications.funnels.referral_bonus import ReferralBonusFunnel
        from services.notifications.funnels.referral_reminder import ReferralReminderFunnel
        from services.notifications.rate_limiter import RateLimiter

        logger.info("Запуск цикла уведомлений")
        try:
            rate_limiter = RateLimiter()
            funnel_manager = FunnelManager(service_data=self._service_data)
            funnel_manager.register(KeyExpiryFunnel24h(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(KeyExpiryFunnel10h(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(KeyExpiredGraceFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(TrialReminderFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(ColdLeadFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(ReferralBonusFunnel(pool=self._pool, rate_limiter=rate_limiter))
            funnel_manager.register(ReferralReminderFunnel(pool=self._pool, rate_limiter=rate_limiter))

            report = await funnel_manager.run_cycle()
            logger.info(
                "Цикл уведомлений завершён",
                users=report.total_users,
                keys_segmented=report.total_keys_segmented,
                duration=f"{report.duration_seconds:.1f}s",
                results=report.results_by_funnel,
            )
        except Exception as e:
            logger.error("Ошибка в цикле уведомлений", error=str(e), exc_info=True)


def create_scheduler(
    service_data: ServiceDataModel,
    pool: asyncpg.Pool,
) -> AsyncIOScheduler:
    """
    Создает и настраивает APScheduler с использованием SyncScheduler.

    SyncScheduler инкапсулирует состояние и предотвращает race conditions,
    гарантируя атомарность проверки и выполнения синхронизации.
    """
    scheduler = AsyncIOScheduler()

    # Создаем SyncScheduler — инкапсулирует состояние и логику
    sync_scheduler = SyncScheduler(service_data=service_data, pool=pool)

    # Сохраняем в scheduler для доступа извне (например, для admin endpoint)
    scheduler.sync_scheduler = sync_scheduler  # type: ignore

    # Кэш загружается при старте приложения (в lifespan)
    # Планировщик только обновляет его каждые 3 часа
    scheduler.add_job(
        sync_scheduler.sync_cache,
        "interval",
        hours=3,
        id="sync_cache",
        replace_existing=True,
        coalesce=True,  # Объединять пропущенные запуски
    )

    # Уведомления каждые 1 час
    scheduler.add_job(
        sync_scheduler.run_notifications,
        "interval",
        hours=1,
        id="notifications",
        replace_existing=True,
    )
    return scheduler
