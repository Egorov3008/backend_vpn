"""Одноразовый запуск цикла уведомлений вне расписания.

Реплицирует backend/app/main.py lifespan (pool + cache + load + service_data)
и вызывает SyncScheduler.run_notifications(), затем корректно завершается.
Запуск: docker compose exec backend python scripts/run_funnel_once.py
"""

import asyncio
import sys
from pathlib import Path

# Запуск из scripts/ — добавляем корень backend (/app) в sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from background.scheduler import SyncScheduler
from database.base import create_db_pool
from database.service import DataService
from logger import setup_logging
from services.cache.loader import LoadingService
from services.cache.service import CacheService
from services.cache.storage import CacheStorage
from services.core.data.service import ServiceDataModel
from config import settings


async def main() -> None:
    setup_logging(
        log_level=settings.log_level,
        log_file=settings.log_file or None,
        log_format=settings.log_format,
    )

    pool = await create_db_pool()
    storage = CacheStorage()
    await storage.start()
    cache_service = CacheService(storage)

    try:
        data_service = DataService()
        loader = LoadingService(cache=cache_service, data_service=data_service, pool=pool)
        await loader.loading()
        service_data = ServiceDataModel(cache_service=cache_service, data_service=data_service)

        sched = SyncScheduler(service_data=service_data, pool=pool)
        await sched.run_notifications()
    finally:
        await storage.stop()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())