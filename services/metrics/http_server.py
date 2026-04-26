"""
HTTP endpoint /metrics для Prometheus scraping.

Поднимается как aiohttp handler рядом с webhook сервером
или на отдельном порту через start_metrics_server().
"""

import logging

from aiohttp import web
from prometheus_client import generate_latest

from services.metrics.registry import REGISTRY


class _Http2Filter(logging.Filter):
    """Фильтрует ERROR-логи от HTTP/2 клиентов (браузеры).

    aiohttp не поддерживает HTTP/2 и бросает BadHttpMessage при получении
    HTTP/2 preface (PRI * HTTP/2.0). Это не ошибка — просто несовместимый клиент.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "PRI" in msg and "HTTP/2" in msg:
            return False
        if hasattr(record, "exc_info") and record.exc_info:
            exc = record.exc_info[1]
            if exc and "PRI" in str(exc):
                return False
        return True


def _install_http2_filter() -> None:
    """Устанавливает фильтр на логгер aiohttp.web_protocol."""
    proto_logger = logging.getLogger("aiohttp.web_protocol")
    proto_logger.addFilter(_Http2Filter())


async def metrics_handler(request: web.Request) -> web.Response:
    """Handler для GET /metrics."""
    output = generate_latest(REGISTRY)
    return web.Response(
        body=output,
        content_type="text/plain",
        charset="utf-8",
    )


def setup_metrics_routes(app: web.Application) -> None:
    """Регистрирует /metrics route в существующем aiohttp приложении."""
    app.router.add_get("/metrics", metrics_handler)


async def start_metrics_server(host: str = "0.0.0.0", port: int = 9090) -> None:
    """Запускает отдельный HTTP сервер для метрик."""
    _install_http2_filter()
    app = web.Application()
    setup_metrics_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
