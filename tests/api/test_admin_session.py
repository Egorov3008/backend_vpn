"""Вход в веб-админку через Mini App админ-бота и работа админ-сессии.

Проверки авторизации не переопределяются — тестируются настоящие
verify_admin_or_bot / verify_admin_actor.
"""
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

import config
from app.core.miniapp_session import admin_sessions, issue_session_token, web_sessions
from app.dependencies import get_cache, get_pool, get_service_data
from app.main import app
from config import settings

ADMIN_BOT_TOKEN = "777:admin-bot-token"
USER_BOT_TOKEN = "111:user-bot-token"
ADMIN = 5001
NOT_ADMIN = 5002


def init_data(user_id=ADMIN, bot_token=ADMIN_BOT_TOKEN, auth_date=None):
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "user": json.dumps({"id": user_id, "first_name": "Admin"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def pool():
    p = AsyncMock(spec=asyncpg.Pool)
    conn = AsyncMock()
    p.acquire = MagicMock()
    p.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    p.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    p.conn = conn
    return p


@pytest.fixture
async def client(mock_service_data, pool, monkeypatch):
    monkeypatch.setattr(settings, "admin_bot_token", ADMIN_BOT_TOKEN)
    monkeypatch.setattr(settings, "bot_token", USER_BOT_TOKEN)
    monkeypatch.setattr(settings, "admin_api_key", "t_admin_key_x1")
    monkeypatch.setattr(settings, "bot_secret_key", "t_bot_secret_x1")
    monkeypatch.setattr(config, "ADMIN_ID", [ADMIN])
    mock_service_data.keys.get_data = AsyncMock(return_value=None)
    app.dependency_overrides[get_service_data] = lambda: mock_service_data
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_cache] = lambda: MagicMock()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def login(client, **kwargs):
    return await client.post("/api/v1/admin/session", json={"init_data": init_data(**kwargs)})


async def test_admin_gets_session_and_login_is_audited(client, pool):
    resp = await login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tg_id"] == ADMIN
    assert body["expires_in"] == settings.admin_session_ttl_seconds
    assert admin_sessions.verify(body["access_token"]) == ADMIN
    args = pool.conn.execute.await_args.args
    assert args[1:] == (ADMIN, "admin_login", None)


async def test_not_admin_is_rejected(client):
    resp = await login(client, user_id=NOT_ADMIN)
    assert resp.status_code == 403


async def test_init_data_of_user_bot_is_rejected(client):
    resp = await login(client, bot_token=USER_BOT_TOKEN)
    assert resp.status_code == 401


async def test_stale_init_data_is_rejected(client):
    old = int(time.time()) - settings.admin_init_data_max_age_seconds - 60
    resp = await login(client, auth_date=old)
    assert resp.status_code == 401


async def test_login_closed_without_admin_bot_token(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_bot_token", "")
    resp = await login(client)
    assert resp.status_code == 503


async def test_session_opens_read_endpoints(client):
    token = (await login(client)).json()["access_token"]
    resp = await client.get("/api/v1/admin/users", headers=bearer(token))
    assert resp.status_code == 200


async def test_destructive_call_audits_tg_id_from_token_not_header(client, pool):
    token, _ = admin_sessions.issue(ADMIN)
    resp = await client.post(
        "/api/v1/admin/keys/renewals",
        json={"emails": ["a@b.com"], "days": 30},
        # Подставленный заголовок игнорируется: tg_id берётся из токена.
        headers={**bearer(token), "X-Admin-Tg-Id": "999"},
    )
    assert resp.status_code == 200
    audited = [c.args[1] for c in pool.conn.execute.await_args_list]
    assert audited and set(audited) == {ADMIN}


@pytest.mark.parametrize(
    "token",
    [
        lambda: web_sessions.issue(ADMIN)[0],
        lambda: issue_session_token(ADMIN)[0],
        lambda: "garbage.token",
    ],
    ids=["web-session", "miniapp-session", "garbage"],
)
async def test_other_tokens_are_not_admin_sessions(client, token):
    for method, url in (("GET", "/api/v1/admin/users"), ("DELETE", "/api/v1/admin/keys/a@b.com")):
        resp = await client.request(method, url, headers=bearer(token()))
        assert resp.status_code == 401, (method, url)


async def test_session_stops_working_when_removed_from_admin_id(client, monkeypatch):
    token, _ = admin_sessions.issue(ADMIN)
    monkeypatch.setattr(config, "ADMIN_ID", [])
    resp = await client.get("/api/v1/admin/users", headers=bearer(token))
    assert resp.status_code == 403


async def test_bot_api_key_still_works(client):
    resp = await client.delete(
        "/api/v1/admin/keys/a@b.com",
        headers={"X-API-Key": "t_admin_key_x1", "X-Admin-Tg-Id": "7"},
    )
    # До хендлера дошли (ключа нет → 404), авторизация пройдена.
    assert resp.status_code == 404


async def test_wrong_api_key_is_rejected_even_with_valid_session(client):
    token, _ = admin_sessions.issue(ADMIN)
    resp = await client.get(
        "/api/v1/admin/users",
        headers={**bearer(token), "X-API-Key": "wrong"},
    )
    assert resp.status_code == 401
