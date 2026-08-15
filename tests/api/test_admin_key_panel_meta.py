"""Tests: admin endpoint for changing panel-only client metadata (group/comment).

POST /api/v1/admin/keys/{email}/panel-meta must call
xui.update_standalone_client(email, **overrides) with only the provided
fields, 404 if the key is unknown, and 400 if neither field is given.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import AdminPrincipal
from app.dependencies import get_cache, get_pool, get_service_data
from app.main import app
from services.cache.service import CacheService


def _make_key(email: str = "user@example.com"):
    key = MagicMock()
    key.email = email
    return key


def _make_principal() -> AdminPrincipal:
    return AdminPrincipal(admin_tg_id=123456)


def _wire_app(pool, service_data, cache, principal):
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_service_data] = lambda: service_data
    app.dependency_overrides[get_cache] = lambda: cache
    # verify_admin_actor is router-level; bypass via principal fixture.
    app.dependency_overrides[__import__("app.auth", fromlist=["verify_admin_actor"]).verify_admin_actor] = lambda: principal


def _headers():
    return {
        "X-Bot-Secret": "test",
        "X-API-Key": "test_admin_key",
        "X-Admin-Tg-Id": "123456",
    }


def _build_xui_mock():
    xui = MagicMock()
    xui.update_standalone_client = AsyncMock(return_value={"success": True})
    return xui


@pytest.fixture
def client_ctx():
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    cache = MagicMock(spec=CacheService)
    service_data = MagicMock()
    service_data.keys.get_data = AsyncMock(return_value=_make_key())
    service_data.data_service.keys.get = AsyncMock(return_value=_make_key())
    principal = _make_principal()
    _wire_app(pool, service_data, cache, principal)

    with patch("api.v1.admin.build_key_services") as build:
        xui = _build_xui_mock()
        build.return_value = (MagicMock(), MagicMock(), xui)
        yield TestClient(app), xui

    app.dependency_overrides.clear()


def test_panel_meta_updates_group_and_comment(client_ctx):
    client, xui = client_ctx
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/panel-meta",
        json={"group": "vip", "comment": "test client"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "email": "user@example.com",
        "group": "vip",
        "comment": "test client",
    }
    xui.update_standalone_client.assert_awaited_once_with(
        "user@example.com", group="vip", comment="test client"
    )


def test_panel_meta_updates_only_group(client_ctx):
    client, xui = client_ctx
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/panel-meta",
        json={"group": "vip"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    xui.update_standalone_client.assert_awaited_once_with(
        "user@example.com", group="vip"
    )


def test_panel_meta_updates_only_comment(client_ctx):
    client, xui = client_ctx
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/panel-meta",
        json={"comment": "test client"},
        headers=_headers(),
    )
    assert resp.status_code == 200
    xui.update_standalone_client.assert_awaited_once_with(
        "user@example.com", comment="test client"
    )


def test_panel_meta_rejects_empty_body(client_ctx):
    client, xui = client_ctx
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/panel-meta",
        json={},
        headers=_headers(),
    )
    assert resp.status_code == 400
    xui.update_standalone_client.assert_not_awaited()


def test_panel_meta_404_when_key_missing(client_ctx):
    client, xui = client_ctx
    service_data = app.dependency_overrides[get_service_data]()
    service_data.keys.get_data = AsyncMock(return_value=None)
    service_data.data_service.keys.get = AsyncMock(return_value=None)
    resp = client.post(
        "/api/v1/admin/keys/missing@example.com/panel-meta",
        json={"group": "vip"},
        headers=_headers(),
    )
    assert resp.status_code == 404
    xui.update_standalone_client.assert_not_awaited()


def test_panel_meta_500_when_panel_call_fails(client_ctx):
    client, xui = client_ctx
    xui.update_standalone_client.side_effect = Exception("panel unavailable")
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/panel-meta",
        json={"group": "vip"},
        headers=_headers(),
    )
    assert resp.status_code == 500
