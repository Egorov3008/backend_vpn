"""Публичный REST API для внешних клиентов (Этап 4).

Единая auth-схема для внешних клиентов (см. app/auth.py::verify_api_client):
per-client API-ключ (Authorization: Bearer <key>) с scopes и опциональной
привязкой к домену (api_clients.allowed_domain — см. app/auth.py). Каждый
под-роутер переиспользует handler-функции внутренних api/v1/*.py — бизнес-
логика не дублируется, дублируется только auth-зависимость.

Только этот префикс (/api/v1/public/*) проксируется наружу обоими nginx-
edge'ами (см. CLAUDE.md, External API clients) — остальные внутренние
роутеры недоступны из интернета независимо от их auth.
"""
from fastapi import APIRouter

from api.v1.public import auth, keys, payments, tariffs, users

router = APIRouter(prefix="/public", tags=["public-api"])
router.include_router(tariffs.router)
router.include_router(keys.router)
router.include_router(payments.router)
router.include_router(users.router)
router.include_router(auth.router)
