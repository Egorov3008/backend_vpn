"""
Кастомный Collector для метрик asyncpg пула соединений.

На каждом scrape читает текущее состояние пула.
"""

import asyncpg
from prometheus_client.core import GaugeMetricFamily


class DBPoolMetricsCollector:
    """Собирает метрики asyncpg.Pool: size, idle, used."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def collect(self):
        size = self._pool.get_size()
        idle = self._pool.get_idle_size()
        used = size - idle

        g_size = GaugeMetricFamily(
            "vpn_db_pool_size", "Размер пула соединений БД"
        )
        g_size.add_metric([], size)
        yield g_size

        g_free = GaugeMetricFamily(
            "vpn_db_pool_free", "Свободные соединения в пуле БД"
        )
        g_free.add_metric([], idle)
        yield g_free

        g_used = GaugeMetricFamily(
            "vpn_db_pool_used", "Используемые соединения в пуле БД"
        )
        g_used.add_metric([], used)
        yield g_used

    def describe(self):
        return []
