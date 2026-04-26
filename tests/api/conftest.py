import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock

from app.main import app
from app.auth import verify_bot_secret, verify_api_key
from app.dependencies import get_service_data, get_pool, get_cache


@pytest.fixture
def mock_service_data():
    sd = MagicMock()
    sd.tariffs.get_all = AsyncMock(return_value=[])
    sd.tariffs.get_data = AsyncMock(return_value=None)
    sd.users.get_data = AsyncMock(return_value=None)
    sd.users.get_all = AsyncMock(return_value=[])
    sd.users.save_data = AsyncMock(return_value=None)
    sd.users.update = AsyncMock(return_value=None)
    sd.keys.get_by = AsyncMock(return_value=None)
    sd.keys.get_data = AsyncMock(return_value=None)
    sd.keys.get_all = AsyncMock(return_value=[])
    sd.payments.save_data = AsyncMock(return_value=None)
    return sd


@pytest.fixture
async def api_client(mock_service_data):
    app.dependency_overrides[get_service_data] = lambda: mock_service_data
    app.dependency_overrides[get_pool] = lambda: AsyncMock()
    app.dependency_overrides[get_cache] = lambda: MagicMock()
    app.dependency_overrides[verify_bot_secret] = lambda: None
    app.dependency_overrides[verify_api_key] = lambda: None

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()
