"""APScheduler setup for backend background tasks."""

import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from logger import logger
from services.core.data.service import ServiceDataModel


def create_scheduler(
    service_data: ServiceDataModel,
    pool: asyncpg.Pool,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        _sync_cache,
        "interval",
        hours=3,
        args=[service_data, pool],
        id="sync_cache",
        replace_existing=True,
    )
    scheduler.add_job(
        _sync_panel,
        "interval",
        hours=3,
        args=[service_data, pool],
        id="sync_panel",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_notifications,
        "interval",
        hours=1,
        args=[service_data, pool],
        id="notifications",
        replace_existing=True,
    )
    return scheduler


async def _sync_cache(service_data: ServiceDataModel, pool: asyncpg.Pool) -> dict:
    from database.service import DataService
    from services.cache.loader import LoadingService

    logger.info("Начало синхронизации кеша")
    try:
        data_service = DataService()
        loader = LoadingService(
            cache=service_data.cache_service,
            data_service=data_service,
            pool=pool,
        )
        await loader.loading()
        logger.info("Синхронизация кеша завершена")
        return {"status": "success", "message": "Cache synchronized"}
    except Exception as e:
        logger.error("Ошибка синхронизации кеша", error=str(e))
        return {"status": "error", "error": str(e)}


async def _sync_panel(service_data: ServiceDataModel, pool: asyncpg.Pool) -> None:
    """Sync panel clients with DB+cache and clean up orphaned keys."""
    from client import XUISession
    from services.cache.loader import LoadingService
    from services.synchron.database_synchronizer import DatabaseSynchronizer
    from services.synchron.xui_fetcher import XUIFetcher
    from services.synchron.cache_comparator import CacheComparator
    from services.synchron.key_creator import KeyCreator
    from services.synchron.traffic import TrafficUpdater
    from database.service import DataService

    logger.info("Начало синхронизации с панелью")
    try:
        data_service = DataService()
        loader = LoadingService(
            cache=service_data.cache_service,
            data_service=data_service,
            pool=pool,
        )
        xui = XUISession(model_service=service_data, loading=loader)
        synchronizer = DatabaseSynchronizer(
            xui_fetcher=XUIFetcher(),
            cache_comparator=CacheComparator(),
            key_creator=KeyCreator(service_data, pool),
            traffic_updater=TrafficUpdater(),
            model_data=service_data,
            pool=pool,
        )
        stats = await synchronizer.sync_data(xui_session=xui)
        logger.info("Синхронизация с панелью завершена", **stats)
        return {"status": "success", **stats}
    except Exception as e:
        logger.error("Ошибка синхронизации с панелью", error=str(e), exc_info=True)
        return {"status": "error", "error": str(e)}


async def _run_notifications(service_data: ServiceDataModel, pool: asyncpg.Pool) -> None:
    """Run notification funnels cycle."""
    from services.notifications.manager import FunnelManager
    from services.notifications.funnels.key_expiry_24h import KeyExpiryFunnel24h
    from services.notifications.funnels.key_expiry_10h import KeyExpiryFunnel10h
    from services.notifications.funnels.trial_reminder import TrialReminderFunnel
    from services.notifications.funnels.cold_lead import ColdLeadFunnel
    from services.notifications.funnels.referral_bonus import ReferralBonusFunnel
    from services.notifications.funnels.referral_reminder import ReferralReminderFunnel
    from services.notifications.rate_limiter import RateLimiter

    logger.info("Запуск цикла уведомлений")
    try:
        rate_limiter = RateLimiter()
        funnel_manager = FunnelManager(service_data=service_data)
        funnel_manager.register(KeyExpiryFunnel24h(pool=pool, rate_limiter=rate_limiter))
        funnel_manager.register(KeyExpiryFunnel10h(pool=pool, rate_limiter=rate_limiter))
        funnel_manager.register(TrialReminderFunnel(pool=pool, rate_limiter=rate_limiter))
        funnel_manager.register(ColdLeadFunnel(pool=pool, rate_limiter=rate_limiter))
        funnel_manager.register(ReferralBonusFunnel(pool=pool, rate_limiter=rate_limiter))
        funnel_manager.register(ReferralReminderFunnel(pool=pool, rate_limiter=rate_limiter))

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
