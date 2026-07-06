"""Task 6: Деструктивные admin-эндпоинты требуют verify_admin_actor (X-API-Key).

Покрывает:
- mass-renew с только X-Bot-Secret → 401 (bot-secret больше не принимается).
- mass-renew с X-API-Key + X-Admin-Tg-Id → 200.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock

import asyncpg

from app.auth import verify_admin_actor
from app.dependencies import get_service_data, get_pool, get_cache
from app.main import app
from config import settings


@pytest.fixture
def mock_pool():
    return AsyncMock(spec=asyncpg.Pool)


@pytest.fixture
def mock_cache():
    return MagicMock()


@pytest.mark.asyncio
async def test_mass_renew_rejects_bot_secret(
    mock_service_data, mock_pool, mock_cache, monkeypatch
):
    """Bot-secret проходит router-level verify_admin_or_bot, но деструктивный
    эндпоинт требует verify_admin_actor (только X-API-Key) → 401."""
    monkeypatch.setattr(settings, "bot_secret_key", "bot-secret-only")
    app.dependency_overrides[get_service_data] = lambda: mock_service_data
    app.dependency_overrides[get_pool] = lambda: mock_pool
    app.dependency_overrides[get_cache] = lambda: mock_cache
    # НЕ оверрайдим verify_admin_actor → реальная проверка
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/admin/keys/mass-renew",
                json={"emails": ["a@b.com"], "days": 30},
                headers={"X-Bot-Secret": "bot-secret-only"},
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mass_renew_accepts_api_key(
    mock_service_data, mock_pool, mock_cache, monkeypatch
):
    """X-API-Key + X-Admin-Tg-Id → 200, audit пишется."""
    monkeypatch.setattr(settings, "admin_api_key", "admin-key")
    mock_service_data.keys.get_data = AsyncMock(return_value=None)
    app.dependency_overrides[get_service_data] = lambda: mock_service_data
    app.dependency_overrides[get_pool] = lambda: mock_pool
    app.dependency_overrides[get_cache] = lambda: mock_cache
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/admin/keys/mass-renew",
                json={"emails": ["a@b.com"], "days": 30},
                headers={"X-API-Key": "admin-key", "X-Admin-Tg-Id": "7"},
            )
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()