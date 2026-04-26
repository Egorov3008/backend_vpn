from fastapi import APIRouter

from api.v1 import tariffs, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(tariffs.router)
api_router.include_router(users.router)
