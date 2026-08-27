import time
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.v1.router import api_router
from background.scheduler import create_scheduler
from config import settings  # noqa: F401
from database.base import create_db_pool
from database.service import DataService
from logger import generate_trace_id, logger, reset_trace_id, set_trace_id, setup_logging
from services.cache.loader import LoadingService
from services.cache.service import CacheService
from services.cache.storage import CacheStorage
from services.core.data.service import ServiceDataModel

# Инициализируем логирование при импорте модуля
setup_logging(
    log_level=settings.log_level,
    log_file=settings.log_file or None,
    log_format=settings.log_format,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Database pool
    pool = await create_db_pool()
    app.state.pool = pool

    # 2. Cache layer
    storage = CacheStorage()
    await storage.start()
    cache_service = CacheService(storage)
    app.state.cache = cache_service

    try:
        # 3. Load initial data from DB into cache
        data_service = DataService()
        loader = LoadingService(cache=cache_service, data_service=data_service, pool=pool)
        await loader.loading()

        # 4. High-level service data model
        service_data = ServiceDataModel(cache_service=cache_service, data_service=data_service)
        app.state.service_data = service_data

        # 5. Background scheduler
        scheduler = create_scheduler(service_data=service_data, pool=pool)
        scheduler.start()
        app.state.scheduler = scheduler
        # SyncScheduler instance for admin/sync async-launch endpoint.
        # create_scheduler() вешает sync_scheduler атрибутом на AsyncIOScheduler.
        app.state.sync_scheduler = scheduler.sync_scheduler  # type: ignore[attr-defined]
    except Exception:
        await storage.stop()
        await pool.close()
        raise

    yield

    # Teardown (reverse order)
    scheduler.shutdown()
    await storage.stop()
    await pool.close()


OPENAPI_TAGS = [
    {
        "name": "auth",
        "description": "Регистрация по инвайт-токену и вход через Telegram Login Widget (web-кабинет).",
    },
    {
        "name": "users",
        "description": "Учётные записи пользователей платформы (bot/web → backend).",
    },
    {
        "name": "keys",
        "description": "VPN-ключи: создание, продление, удаление, триал, канальный бонус.",
    },
    {
        "name": "payments",
        "description": "Платежи YooKassa: создание счёта, история, webhook.",
    },
    {
        "name": "tariffs",
        "description": "Тарифные планы.",
    },
    {
        "name": "landing",
        "description": "Анонимный поток лендинга (24h-ключи без регистрации, реферальные cookie).",
    },
    {
        "name": "admin",
        "description": "Административные операции (X-API-Key и/или X-Bot-Secret). "
        "Деструктивные операции (удаление, mass-renew и т.п.) требуют X-API-Key.",
    },
    {
        "name": "mobile-mvp",
        "description": "Единый shared-конфиг для MVP-версии Android-приложения (X-App-Secret).",
    },
    {
        "name": "public-api",
        "description": "Публичный REST API для внешних клиентов (не bot/web/mobile-mvp). "
        "Аутентификация — персональный API-ключ с scopes: `Authorization: Bearer <key>`. "
        "Ключи выдаются/отзываются через /admin/api-clients (X-API-Key).",
    },
]

app = FastAPI(
    title="VPN Platform Backend",
    description=(
        "Внутренний REST API платформы VPN-сервиса. Источник истины для бизнес-логики "
        "ключей, платежей и тарифов; клиенты — Telegram-бот, web-кабинет и MVP-приложение. "
        "Аутентификация — общие секреты в заголовках (см. security schemes ниже), "
        "не OAuth/per-client API-ключи."
    ),
    version="1.0.0",
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)
app.include_router(api_router)

# Vanilla-JS admin panel (admin_panel/) — separate from /api/v1/admin/* (the API
# it consumes). No CORS needed: same origin as the API.
app.mount(
    "/admin-panel",
    StaticFiles(directory=Path(__file__).parent.parent / "admin_panel", html=True),
    name="admin_panel",
)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """Генерирует trace_id для каждого HTTP-запроса и пишет access-log
    (метод/путь/статус/латентность/trace_id)."""
    trace_id = generate_trace_id()
    # Дублируем в request.state: catch-all Exception handler ниже обслуживается
    # ServerErrorMiddleware (снаружи этого middleware) и вызывается уже ПОСЛЕ
    # срабатывания finally/reset_trace_id() здесь — к этому моменту contextvar
    # уже сброшен, а request.state — нет.
    request.state.trace_id = trace_id
    set_trace_id(trace_id)
    start = time.monotonic()
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        logger.info(
            "HTTP request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.monotonic() - start) * 1000, 1),
        )
        return response
    except Exception:
        # Тело ошибки формирует unhandled_exception_handler ниже (всегда 500);
        # здесь — только access-log-строка для этого запроса.
        logger.error(
            "HTTP request failed",
            method=request.method,
            path=request.url.path,
            status_code=500,
            duration_ms=round((time.monotonic() - start) * 1000, 1),
        )
        raise
    finally:
        reset_trace_id()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Единый формат для всех HTTPException — сохраняет `detail` как есть
    (строка или dict, см. admin.py sync-conflict) для обратной совместимости
    с тестами, добавляет `trace_id` рядом."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "trace_id": getattr(request.state, "trace_id", "")},
        headers={**(exc.headers or {}), "X-Trace-Id": getattr(request.state, "trace_id", "")},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "trace_id": getattr(request.state, "trace_id", "")},
        headers={"X-Trace-Id": getattr(request.state, "trace_id", "")},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Ловит всё, что не поймано роутером — не даёт утечь трейсбеку/деталям
    исключения наружу; полная информация уходит только в лог."""
    trace_id = getattr(request.state, "trace_id", "")
    logger.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "trace_id": trace_id},
        headers={"X-Trace-Id": trace_id},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "backend"}


@app.get("/readiness")
async def readiness(request: Request):
    try:
        pool: asyncpg.Pool = request.app.state.pool
        await pool.fetchval("SELECT 1")
        return {"status": "ready", "db": "connected"}
    except Exception as e:
        from logger import logger
        logger.error("Readiness check failed", error=str(e))
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "error": "database unavailable"},
        )
