from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.v1.router import api_router
from background.scheduler import create_scheduler
from config import settings  # noqa: F401
from database.base import create_db_pool
from database.service import DataService
from logger import generate_trace_id, get_trace_id, reset_trace_id, set_trace_id, setup_logging
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
    except Exception:
        await storage.stop()
        await pool.close()
        raise

    yield

    # Teardown (reverse order)
    scheduler.shutdown()
    await storage.stop()
    await pool.close()


app = FastAPI(title="VPN Platform Backend", lifespan=lifespan)
app.include_router(api_router)


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """Генерирует trace_id для каждого HTTP-запроса."""
    trace_id = generate_trace_id()
    set_trace_id(trace_id)
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
    finally:
        reset_trace_id()


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
