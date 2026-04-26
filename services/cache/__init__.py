from services.cache.storage import CacheStorage
from services.cache.service import CacheService
from services.cache.loader import LoadingService
from database.service import DataService
import asyncio
from logger import logger


async def run_cache_service():
    # Создаем хранилище
    storage = CacheStorage()

    # Создаем сервис кеширования
    cache_service = CacheService(storage=storage)

    # Запускаем цикл очистки
    await cache_service.start()
    logger.info("CacheService запущен")

    # Опционально: загружаем данные в кеш
    data_service = DataService()
    loader = LoadingService(cache=cache_service, data_service=data_service)
    await loader.loading()

    # Здесь можно продолжить выполнение приложения
    # Например, держать кеш запущенным
    try:
        while True:
            await asyncio.sleep(3600)  # спим, чтобы приложение не завершилось
    except KeyboardInterrupt:
        logger.info("Остановка CacheService...")
        await cache_service.stop()
