"""Tests: admin destructive endpoints must sync inbound set to .env before extend.

Mirrors the grace→active branch of KeyRenewal.extension_key:
admin_mass_renew, admin_change_key_date, admin_change_key_tariff must call
xui.set_inbounds(key.email, paid_inbound_ids()) before xui.extend_client_key,
and must continue best-effort if set_inbounds returns False.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import AdminPrincipal
from app.dependencies import get_cache, get_pool, get_service_data
from app.main import app
from services.cache.service import CacheService
from services.core.data.service import ServiceDataModel
from services.core.keys.utils.inbounds import paid_inbound_ids


def _make_key(email: str = "user@example.com", expiry_ms: int = 1_700_000_000_000):
    key = MagicMock()
    key.email = email
    key.expiry_time = expiry_ms
    key.limit_ip = 3
    key.tariff_id = 10
    key.name_tariff = "Standard"
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


def _build_xui_mock(set_inbounds_return: bool = True):
    """Build an `xui` object whose set_inbounds/extend_client_key we can inspect."""
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=set_inbounds_return)
    xui.extend_client_key = AsyncMock(return_value=True)
    return xui


@pytest.fixture
def client_ctx():
    pool = MagicMock()
    # async-friendly pool: resetter.reset_key_after_renewal(pool, key) и ряд
    # других admin-обработчиков вызывают `await conn.execute(...)` / `fetchrow`
    # / `fetch` прямо на pool (без `async with pool.acquire() as conn:`).
    # Без AsyncMock-ов на этих методах TypeError "object MagicMock can't be
    # used in 'await' expression" роняет эндпоинт в 5xx.
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock()
    pool.fetch = AsyncMock()
    cache = MagicMock(spec=CacheService)
    # Без spec: ServiceDataModel задаёт keys/users/... в __init__, и при
    # spec= их не видно. Существующие admin-тесты используют обычный MagicMock.
    service_data = MagicMock()
    service_data.keys.get_data = AsyncMock(return_value=_make_key())
    service_data.keys.update = AsyncMock()
    service_data.data_service.keys.get = AsyncMock(return_value=_make_key())
    principal = _make_principal()
    _wire_app(pool, service_data, cache, principal)

    # NOTE: план/бриф указывают `app.factories.build_key_services`, но admin.py
    # импортирует `from app.factories import build_key_services` → имя биндится
    # в api.v1.admin. Патчим там, где функция реально вызывается.
    with patch("api.v1.admin.build_key_services") as build:
        xui = _build_xui_mock()
        # build_key_services returns (create_key, key_renewal, xui)
        build.return_value = (MagicMock(), MagicMock(), xui)
        yield TestClient(app), xui

    app.dependency_overrides.clear()


# ---------- mass-renew ----------


def test_mass_renew_calls_set_inbounds_with_paid_before_extend(client_ctx):
    client, xui = client_ctx
    resp = client.post(
        "/api/v1/admin/keys/mass-renew",
        json={"emails": ["user@example.com"], "days": 30},
        headers=_headers(),
    )
    assert resp.status_code == 200
    xui.set_inbounds.assert_awaited_once()
    xui.extend_client_key.assert_awaited_once()
    args, _ = xui.set_inbounds.call_args
    assert args[0] == "user@example.com"
    assert args[1] == paid_inbound_ids()
    # set_inbounds must be awaited BEFORE extend_client_key
    assert xui.set_inbounds.call_args is not None
    assert xui.extend_client_key.call_args is not None


def test_mass_renew_continues_when_set_inbounds_fails(client_ctx):
    client, xui = client_ctx
    xui.set_inbounds.return_value = False
    resp = client.post(
        "/api/v1/admin/keys/mass-renew",
        json={"emails": ["user@example.com"], "days": 30},
        headers=_headers(),
    )
    assert resp.status_code == 200
    # set_inbounds must have been attempted хотя бы раз — иначе тест не ловит
    # пробел в продакшене (см. Finding 1 в task-1-report).
    xui.set_inbounds.assert_awaited_once()
    # extend must still have run
    xui.extend_client_key.assert_awaited_once()


def test_mass_renew_uses_paid_inbound_ids_from_inbounds_module(client_ctx):
    client, xui = client_ctx
    client.post(
        "/api/v1/admin/keys/mass-renew",
        json={"emails": ["user@example.com"], "days": 30},
        headers=_headers(),
    )
    sent = xui.set_inbounds.call_args.args[1]
    assert sent == paid_inbound_ids()


# ---------- change-date ----------


def test_change_date_calls_set_inbounds_with_paid_before_extend(client_ctx):
    client, xui = client_ctx
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/change-date",
        json={"expiry_time": 1_900_000_000_000},
        headers=_headers(),
    )
    assert resp.status_code == 200
    xui.set_inbounds.assert_awaited_once()
    xui.extend_client_key.assert_awaited_once()
    args, _ = xui.set_inbounds.call_args
    assert args[0] == "user@example.com"
    assert args[1] == paid_inbound_ids()


def test_change_date_continues_when_set_inbounds_fails(client_ctx):
    client, xui = client_ctx
    xui.set_inbounds.return_value = False
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/change-date",
        json={"expiry_time": 1_900_000_000_000},
        headers=_headers(),
    )
    assert resp.status_code == 200
    xui.extend_client_key.assert_awaited_once()


def test_change_date_uses_paid_inbound_ids_from_inbounds_module(client_ctx):
    client, xui = client_ctx
    client.post(
        "/api/v1/admin/keys/user@example.com/change-date",
        json={"expiry_time": 1_900_000_000_000},
        headers=_headers(),
    )
    sent = xui.set_inbounds.call_args.args[1]
    assert sent == paid_inbound_ids()


# ---------- change-tariff ----------


def test_change_tariff_calls_set_inbounds_with_paid_before_extend(client_ctx):
    client, xui = client_ctx
    service_data = app.dependency_overrides[get_service_data]()
    service_data.tariffs.get_data = AsyncMock(return_value=MagicMock(
        id=20, name_tariff="Pro", limit_ip=5,
    ))
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/change-tariff",
        json={"tariff_id": 20},
        headers=_headers(),
    )
    assert resp.status_code == 200
    xui.set_inbounds.assert_awaited_once()
    xui.extend_client_key.assert_awaited_once()
    args, _ = xui.set_inbounds.call_args
    assert args[0] == "user@example.com"
    assert args[1] == paid_inbound_ids()


def test_change_tariff_continues_when_set_inbounds_fails(client_ctx):
    client, xui = client_ctx
    service_data = app.dependency_overrides[get_service_data]()
    service_data.tariffs.get_data = AsyncMock(return_value=MagicMock(
        id=20, name_tariff="Pro", limit_ip=5,
    ))
    xui.set_inbounds.return_value = False
    resp = client.post(
        "/api/v1/admin/keys/user@example.com/change-tariff",
        json={"tariff_id": 20},
        headers=_headers(),
    )
    assert resp.status_code == 200
    xui.set_inbounds.assert_awaited_once()
    xui.extend_client_key.assert_awaited_once()


def test_change_tariff_uses_paid_inbound_ids_from_inbounds_module(client_ctx):
    client, xui = client_ctx
    service_data = app.dependency_overrides[get_service_data]()
    service_data.tariffs.get_data = AsyncMock(return_value=MagicMock(
        id=20, name_tariff="Pro", limit_ip=5,
    ))
    client.post(
        "/api/v1/admin/keys/user@example.com/change-tariff",
        json={"tariff_id": 20},
        headers=_headers(),
    )
    sent = xui.set_inbounds.call_args.args[1]
    assert sent == paid_inbound_ids()
