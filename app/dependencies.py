import asyncpg
from fastapi import Request

from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def get_cache(request: Request) -> CacheService:
    return request.app.state.cache


def get_service_data(request: Request) -> ServiceDataModel:
    return request.app.state.service_data
