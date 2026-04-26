import pytest
from unittest.mock import AsyncMock, MagicMock
from app.main import readiness


@pytest.mark.asyncio
async def test_readiness_ok():
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=1)
    mock_request = MagicMock()
    mock_request.app.state.pool = mock_pool

    response = await readiness(mock_request)
    assert response == {"status": "ready", "db": "connected"}


@pytest.mark.asyncio
async def test_readiness_db_error():
    from fastapi.responses import JSONResponse

    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(side_effect=Exception("connection refused"))
    mock_request = MagicMock()
    mock_request.app.state.pool = mock_pool

    response = await readiness(mock_request)
    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
