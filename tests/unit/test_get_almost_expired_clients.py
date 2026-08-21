"""Tests for XUISession.get_almost_expired_clients."""
import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from client import XUISession


@pytest.fixture
def xui_session():
    mock_model_service = MagicMock()
    mock_loading = MagicMock()
    return XUISession(
        model_service=mock_model_service,
        loading=mock_loading,
        login_timeout=5.0,
        login_max_retries=1,
    )


@pytest.mark.asyncio
async def test_filters_clients_within_window(xui_session):
    now_ms = time.time() * 1000
    raw_clients = [
        {"email": "expired", "expiryTime": now_ms - 60_000},
        {"email": "in_window_later", "expiryTime": now_ms + 500_000},
        {"email": "in_window_sooner", "expiryTime": now_ms + 100_000},
        {"email": "far_future", "expiryTime": now_ms + 100_000_000},
        {"email": "unlimited", "expiryTime": 0},
        {"email": "unlimited_missing_field"},
    ]
    xui_session.list_clients_all = AsyncMock(return_value=raw_clients)

    result = await xui_session.get_almost_expired_clients(within_seconds=600)

    assert [c.email for c in result] == ["in_window_sooner", "in_window_later"]


@pytest.mark.asyncio
async def test_returns_empty_when_nothing_expiring(xui_session):
    now_ms = time.time() * 1000
    raw_clients = [
        {"email": "expired", "expiryTime": now_ms - 60_000},
        {"email": "unlimited", "expiryTime": 0},
        {"email": "far_future", "expiryTime": now_ms + 100_000_000},
    ]
    xui_session.list_clients_all = AsyncMock(return_value=raw_clients)

    result = await xui_session.get_almost_expired_clients(within_seconds=600)

    assert result == []


@pytest.mark.asyncio
async def test_propagates_page_size_to_list_clients_all(xui_session):
    xui_session.list_clients_all = AsyncMock(return_value=[])

    await xui_session.get_almost_expired_clients(within_seconds=600, page_size=50)

    xui_session.list_clients_all.assert_awaited_once_with(page_size=50)
