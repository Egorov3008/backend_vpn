"""Веб-кабинет: cookie-сессия, вход через бота, анонимный ключ."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import api.v1.web as web_mod
from app.core.miniapp_session import issue_session_token, web_sessions
from app.dependencies import get_cache, get_pool, get_service_data
from app.main import app
from app.schemas.payments import PaymentCreateResponse
from config import settings
from models import Key, Tariff, User
from services.web_auth.service import WebLoginService
from tests.unit.test_web_login_service import FakeStore

TG_ID = 482913
CSRF = {"X-Requested-With": "web"}
BOT_SECRET = "t_bot_secret_web_1"
INTERNAL_SECRET = "t_internal_secret_web_1"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Лимиты в памяти общие на процесс: без сброса тесты входа упираются в 429."""
    from app import rate_limit

    rate_limit._buckets.clear()
    yield
    rate_limit._buckets.clear()


@pytest.fixture
def login_service():
    return WebLoginService(FakeStore(), ttl_seconds=600)


@pytest.fixture
async def client(mock_service_data, login_service, monkeypatch):
    """Клиент без подмены проверок секретов (в отличие от api_client)."""
    from bot_project import bot as telegram_bot

    monkeypatch.setattr(telegram_bot, "_username_cache", "test_bot")
    monkeypatch.setattr(settings, "bot_secret_key", BOT_SECRET)
    monkeypatch.setattr(settings, "landing_internal_secret", INTERNAL_SECRET)
    monkeypatch.setattr("api.v1.cabinet.AVAILABLE_RATES_LIST", [1, 2, 5])

    app.dependency_overrides[get_service_data] = lambda: mock_service_data
    app.dependency_overrides[get_pool] = lambda: AsyncMock()
    app.dependency_overrides[get_cache] = lambda: MagicMock()
    app.dependency_overrides[web_mod.get_web_login_service] = lambda: login_service
    # https: cookie веба ставятся с Secure.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        yield c
    app.dependency_overrides.clear()


def login_as(client, tg_id=TG_ID):
    token, _ = web_sessions.issue(tg_id)
    client.cookies.set("web_session", token)


# --- Сессия -----------------------------------------------------------------


async def test_cabinet_requires_session(client):
    r = await client.get("/api/v1/web/keys")
    assert r.status_code == 401


async def test_miniapp_token_in_cookie_rejected(client):
    token, _ = issue_session_token(TG_ID)
    client.cookies.set("web_session", token)
    r = await client.get("/api/v1/web/keys")
    assert r.status_code == 401


async def test_miniapp_bearer_not_accepted_by_web(client):
    token, _ = issue_session_token(TG_ID)
    r = await client.get("/api/v1/web/keys", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_web_cookie_not_accepted_by_miniapp(client):
    token, _ = web_sessions.issue(TG_ID)
    r = await client.get("/api/v1/miniapp/keys", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_list_keys_uses_session_tg_id(client, mock_service_data):
    login_as(client)
    r = await client.get("/api/v1/web/keys?tg_id=999")
    assert r.status_code == 200
    assert mock_service_data.data_service.keys.filter.await_args.kwargs == {"tg_id": TG_ID}


async def test_mutation_without_csrf_header_rejected(client, mock_service_data):
    login_as(client)
    mock_service_data.keys.get_data = AsyncMock(
        return_value=Key(email="k@x", tg_id=TG_ID, expiry_time=0, key="v", client_id="c", inbound_id=1)
    )
    r = await client.delete("/api/v1/web/keys/k@x")
    assert r.status_code == 403
    mock_service_data.data_service.keys.delete.assert_not_awaited()


async def test_foreign_key_returns_404(client, mock_service_data):
    login_as(client)
    mock_service_data.keys.get_data = AsyncMock(
        return_value=Key(email="k@x", tg_id=1, expiry_time=0, key="v", client_id="c", inbound_id=1)
    )
    r = await client.get("/api/v1/web/keys/k@x")
    assert r.status_code == 404


async def test_me_returns_session_user(client, mock_service_data):
    login_as(client)
    mock_service_data.users.get_data = AsyncMock(return_value=User(tg_id=TG_ID, username="ivan"))
    r = await client.get("/api/v1/web/me")
    assert r.status_code == 200
    assert r.json()["tg_id"] == TG_ID


# --- Оплата -----------------------------------------------------------------


@pytest.mark.parametrize(
    "public_url, expected",
    [("https://app.example.ru/", "https://app.example.ru/pay/result"), ("", None)],
)
async def test_payment_return_url_from_settings(client, mock_service_data, monkeypatch, public_url, expected):
    monkeypatch.setattr(settings, "web_public_url", public_url)
    login_as(client)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=Tariff(id=2, name_tariff="Std", amount=200.0))
    captured = {}

    async def fake_create(body, pool, service_data, cache, *, return_url=None):
        captured["return_url"] = return_url
        captured["tg_id"] = body.tg_id
        return PaymentCreateResponse(
            payment_id="p1", confirmation_url="https://yk", amount=0,
            base_amount=0, volume_discount_percent=0, volume_discount_amount=0,
            referral_discount_amount=0, balance_discount_amount=0, final_amount=0,
            has_volume_discount=False, has_referral_discount=False, has_balance_discount=False,
        )

    monkeypatch.setattr("api.v1.cabinet.create_payment_for_user", fake_create)
    r = await client.post("/api/v1/web/payments", json={"tariff_id": 2, "tg_id": 1}, headers=CSRF)
    assert r.status_code == 200
    assert captured == {"return_url": expected, "tg_id": TG_ID}


# --- Вход через бота ----------------------------------------------------------


def _bot_headers(bot=BOT_SECRET, internal=INTERNAL_SECRET):
    return {"X-Bot-Secret": bot, "X-Landing-Internal-Secret": internal}


async def _start_login(client):
    r = await client.post("/api/v1/web/auth/bot-login", headers=CSRF)
    assert r.status_code == 200, r.text
    deep_link = r.json()["deep_link"]
    assert deep_link.startswith("https://t.me/test_bot?start=weblogin_")
    return r, deep_link.split("weblogin_", 1)[1]


async def test_bot_login_start_sets_strict_httponly_poll_cookie(client):
    r, token = await _start_login(client)
    cookie = r.headers["set-cookie"].lower()
    assert "web_login_poll=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
    assert "path=/api/v1/web/auth" in cookie
    assert len(token) == 32


async def test_bot_login_start_requires_csrf_header(client):
    r = await client.post("/api/v1/web/auth/bot-login")
    assert r.status_code == 403


async def test_full_bot_login_flow(client, mock_service_data):
    _, token = await _start_login(client)

    r = await client.post("/api/v1/web/auth/bot-login/complete", headers=CSRF)
    assert r.json() == {"status": "pending", "tg_id": None}

    r = await client.post(
        f"/api/v1/auth/web-login/{token}/confirm", json={"tg_id": TG_ID}, headers=_bot_headers()
    )
    assert r.status_code == 200
    assert r.json() == {"status": "confirmed", "landing_uid": None}

    r = await client.post("/api/v1/web/auth/bot-login/complete", headers=CSRF)
    assert r.json() == {"status": "confirmed", "tg_id": TG_ID}
    assert "web_session=" in r.headers.get("set-cookie", "")

    mock_service_data.users.get_data = AsyncMock(return_value=User(tg_id=TG_ID, username="ivan"))
    r = await client.get("/api/v1/web/me")
    assert r.status_code == 200
    assert r.json()["tg_id"] == TG_ID


async def test_other_browser_cannot_complete_login(client):
    _, token = await _start_login(client)
    await client.post(f"/api/v1/auth/web-login/{token}/confirm", json={"tg_id": TG_ID}, headers=_bot_headers())

    client.cookies.clear()
    r = await client.post("/api/v1/web/auth/bot-login/complete", headers=CSRF)
    assert r.json()["status"] == "expired"
    assert "web_session=" not in r.headers.get("set-cookie", "")


async def test_bot_login_carries_anonymous_landing_uid(client, monkeypatch):
    monkeypatch.setattr(web_mod.landing, "landing_uid_from_cookie", lambda value: "uid42" if value else None)
    client.cookies.set("tg_landing_id", "signed")
    _, token = await _start_login(client)
    r = await client.post(
        f"/api/v1/auth/web-login/{token}/confirm", json={"tg_id": TG_ID}, headers=_bot_headers()
    )
    assert r.json() == {"status": "confirmed", "landing_uid": "uid42"}


@pytest.mark.parametrize(
    "headers",
    [{}, _bot_headers(bot="wrong"), _bot_headers(internal="wrong"), {"X-Bot-Secret": BOT_SECRET}],
)
async def test_confirm_requires_both_bot_secrets(client, headers):
    _, token = await _start_login(client)
    r = await client.post(f"/api/v1/auth/web-login/{token}/confirm", json={"tg_id": TG_ID}, headers=headers)
    assert r.status_code == 401


async def test_confirm_not_reachable_under_web_prefix(client):
    _, token = await _start_login(client)
    r = await client.post(
        f"/api/v1/web/auth/web-login/{token}/confirm", json={"tg_id": TG_ID}, headers={**CSRF, **_bot_headers()}
    )
    assert r.status_code in (404, 405)


async def test_logout_clears_session_cookie(client):
    login_as(client)
    r = await client.post("/api/v1/web/auth/logout", headers=CSRF)
    assert r.status_code == 204
    assert 'web_session=""' in r.headers["set-cookie"] or "web_session=;" in r.headers["set-cookie"]


# --- Анонимный ключ -----------------------------------------------------------


async def test_anonymous_key_refused_while_previous_alive(client, monkeypatch):
    from api.v1.landing import LandingStateResponse

    monkeypatch.setattr(web_mod.landing, "get_state", AsyncMock(return_value=LandingStateResponse(state="active")))
    create = AsyncMock()
    monkeypatch.setattr(web_mod.landing, "create_quick_key", create)
    r = await client.post("/api/v1/web/anonymous/key", headers=CSRF)
    assert r.status_code == 409
    create.assert_not_awaited()


async def test_anonymous_key_issued_for_new_browser(client, monkeypatch):
    from api.v1.landing import LandingStateResponse, QuickKeyResponse

    monkeypatch.setattr(web_mod.landing, "get_state", AsyncMock(return_value=LandingStateResponse(state="new")))
    create = AsyncMock(return_value=QuickKeyResponse(
        key_value="https://sub/x", expires_at_ms=1, remaining_seconds=1,
        deep_link_happ="happ://add/x", deep_link_bot="https://t.me/b", bot_url="https://t.me/b",
        state="active", landing_uid="uid42",
    ))
    monkeypatch.setattr(web_mod.landing, "create_quick_key", create)
    r = await client.post("/api/v1/web/anonymous/key", headers=CSRF)
    assert r.status_code == 200
    assert r.json()["key_value"] == "https://sub/x"
    create.assert_awaited_once()


async def test_anonymous_key_requires_csrf_header(client):
    r = await client.post("/api/v1/web/anonymous/key")
    assert r.status_code == 403


# --- Лендинг: тарифы и реферальные ссылки ---------------------------------------


async def test_anonymous_tariffs_need_no_session_and_hide_free_and_unlisted(client, mock_service_data):
    mock_service_data.tariffs.get_all = AsyncMock(return_value=[
        Tariff(id=1, name_tariff="Free", amount=0.0),
        Tariff(id=2, name_tariff="Std", amount=200.0, limit_ip=3),
        Tariff(id=3, name_tariff="Hidden", amount=300.0),
    ])
    r = await client.get("/api/v1/web/anonymous/tariffs")
    assert r.status_code == 200
    assert [t["id"] for t in r.json()] == [2]
    assert r.json()[0]["limit_ip"] == 3


REF_TOKEN = "ref_abc123def456"


def _ref_link(referrer=777):
    link = MagicMock()
    link.referrer_tg_id = referrer
    return link


async def test_anonymous_ref_sets_signed_strict_cookie(client, mock_service_data):
    mock_service_data.referral_links.get_by = AsyncMock(return_value=_ref_link())
    r = await client.post("/api/v1/web/anonymous/ref", json={"ref_token": "abc123def456"}, headers=CSRF)
    assert r.status_code == 204
    mock_service_data.referral_links.get_by.assert_awaited_once_with(token=REF_TOKEN)
    cookie = r.headers["set-cookie"].lower()
    assert "tg_ref=" in cookie
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api/v1/web" in cookie
    assert web_mod.landing._verify_ref_cookie(client.cookies.get("tg_ref")) == REF_TOKEN


@pytest.mark.parametrize("token, status", [("not-a-token", 400), (REF_TOKEN, 404)])
async def test_anonymous_ref_rejects_bad_or_unknown_token(client, mock_service_data, token, status):
    mock_service_data.referral_links.get_by = AsyncMock(return_value=None)
    r = await client.post("/api/v1/web/anonymous/ref", json={"ref_token": token}, headers=CSRF)
    assert r.status_code == status
    assert "tg_ref=" not in r.headers.get("set-cookie", "")


async def test_anonymous_ref_requires_csrf_header(client):
    r = await client.post("/api/v1/web/anonymous/ref", json={"ref_token": REF_TOKEN})
    assert r.status_code == 403


async def _confirmed_login(client):
    _, token = await _start_login(client)
    await client.post(f"/api/v1/auth/web-login/{token}/confirm", json={"tg_id": TG_ID}, headers=_bot_headers())
    return await client.post("/api/v1/web/auth/bot-login/complete", headers=CSRF)


async def test_login_attaches_referral_from_cookie(client, monkeypatch):
    attach = AsyncMock(return_value=777)
    monkeypatch.setattr(web_mod.landing, "_attach_referral_if_any", attach)
    client.cookies.set("tg_ref", web_mod.landing._sign_ref_cookie(REF_TOKEN))

    r = await _confirmed_login(client)

    assert r.json()["status"] == "confirmed"
    tg_ref, tg_id = attach.await_args.args[3:]
    assert web_mod.landing._verify_ref_cookie(tg_ref) == REF_TOKEN
    assert tg_id == TG_ID
    assert "tg_ref=" in r.headers.get("set-cookie", "")  # кука снята


async def test_login_without_ref_cookie_skips_referral(client, monkeypatch):
    attach = AsyncMock()
    monkeypatch.setattr(web_mod.landing, "_attach_referral_if_any", attach)
    r = await _confirmed_login(client)
    assert r.json()["status"] == "confirmed"
    attach.assert_not_awaited()


async def test_referral_failure_does_not_break_login(client, monkeypatch):
    monkeypatch.setattr(web_mod.landing, "_attach_referral_if_any", AsyncMock(side_effect=RuntimeError("db down")))
    client.cookies.set("tg_ref", web_mod.landing._sign_ref_cookie(REF_TOKEN))
    r = await _confirmed_login(client)
    assert r.json()["status"] == "confirmed"
    assert "web_session=" in r.headers.get("set-cookie", "")
