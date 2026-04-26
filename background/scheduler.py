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
    return scheduler


async def _sync_cache(service_data: ServiceDataModel, pool: asyncpg.Pool) -> None:
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
    except Exception as e:
        logger.error("Ошибка синхронизации кеша", error=str(e))
