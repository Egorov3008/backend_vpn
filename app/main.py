from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.config import settings  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Stage 1: here will be DB pool, CacheService, APScheduler init
    yield


app = FastAPI(title="VPN Platform Backend", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "backend"}
