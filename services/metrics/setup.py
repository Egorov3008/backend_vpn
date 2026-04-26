"""
Инициализация метрик: регистрация кастомных Collector'ов и запуск /metrics сервера.

Вызывается один раз при старте приложения из on_startup().
"""

import asyncpg
from logger import logger
from services.cache.storage import CacheStorage
from services.metrics.collectors.cache_collector import CacheMetricsCollector
from services.metrics.collectors.db_pool_collector import DBPoolMetricsCollector
from services.metrics.http_server import start_metrics_server
from services.metrics.registry import REGISTRY


async def init_metrics(
    pool: asyncpg.Pool,
    cache_storage: CacheStorage,
    metrics_port: int = 9090,
) -> None:
    """
    Инициализирует систему метрик:
    1. Регистрирует кастомные Collector'ы (cache, db pool)
    2. Запускает HTTP сервер /metrics
    """
    # Кастомные Collector'ы (pull-модель)
    REGISTRY.register(CacheMetricsCollector(cache_storage))
    REGISTRY.register(DBPoolMetricsCollector(pool))

    # HTTP сервер для Prometheus scraping
    await start_metrics_server(port=metrics_port)
    logger.info("Prometheus metrics сервер запущен", port=metrics_port)
