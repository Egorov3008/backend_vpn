"""
Модуль метрик Prometheus для мониторинга VPN-бота.

Предоставляет:
- registry — все Counter/Gauge/Histogram объекты
- middleware — PrometheusMiddleware для aiogram
- collectors — кастомные Collector'ы для кеша и DB pool
- http_server — /metrics endpoint
"""

from services.metrics.registry import REGISTRY

__all__ = ["REGISTRY"]
