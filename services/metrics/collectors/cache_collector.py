"""
Кастомный Collector для метрик кеша.

Использует pull-модель: на каждом scrape /metrics
читает CacheStorage._storage без инструментирования hot path.
"""

from prometheus_client.core import GaugeMetricFamily

from services.cache.storage import CacheStorage


class CacheMetricsCollector:
    """Собирает метрики размера кеша по namespace."""

    def __init__(self, storage: CacheStorage) -> None:
        self._storage = storage

    def collect(self):
        gauge = GaugeMetricFamily(
            "vpn_cache_items_count",
            "Количество элементов в кеше по namespace",
            labels=["namespace"],
        )
        for namespace, items in self._storage._storage.items():
            gauge.add_metric([namespace], len(items))
        yield gauge

    def describe(self):
        return []
