import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest

from api.v1.auth import get_user_repository
from app.core.miniapp_session import issue_session_token, verify_session_token, MiniAppSessionError
from app.core.telegram import TelegramHashError, verify_webapp_init_data
from app.dependencies import get_pool
from app.main import app
from config import settings
from app.schemas.payments import PaymentCreateResponse
from models import Key, Tariff, User

BOT_TOKEN = "123456:TEST-BOT-TOKEN"
TG_ID = 482913


def make_init_data(user_id=TG_ID, auth_date=None, bot_token=BOT_TOKEN, extra=None, tamper=None):
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Ivan", "username": "ivan"}, separators=(",", ":")),
    }
    if extra:
        fields.update(extra)
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields.update(tamper)
    return urlencode(fields)


def bearer(tg_id=TG_ID):
    token, _ = issue_session_token(tg_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _bot_token(monkeypatch):
    monkeypatch.setattr(settings, "bot_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "bot_secret_key", "t_bot_secret_x1")


# --- initData signature ----------------------------------------------------


def test_init_data_valid_signature_returns_user():
    fields = verify_webapp_init_data(make_init_data(), BOT_TOKEN)
    assert fields["user"]["id"] == TG_ID


def test_init_data_wrong_bot_token_rejected():
    with pytest.raises(TelegramHashError, match="Invalid Telegram hash"):
        verify_webapp_init_data(make_init_data(), "999:OTHER")


def test_init_data_tampered_user_rejected():
    tampered = make_init_data(tamper={"user": json.dumps({"id": 1})})
    with pytest.raises(TelegramHashError, match="Invalid Telegram hash"):
        verify_webapp_init_data(tampered, BOT_TOKEN)


def test_init_data_login_widget_hash_not_accepted():
    # Login Widget uses sha256(bot_token) as the key; Mini App uses HMAC("WebAppData").
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": TG_ID})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    widget_secret = hashlib.sha256(BOT_TOKEN.encode()).digest()
    fields["hash"] = hmac.new(widget_secret, check.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(TelegramHashError, match="Invalid Telegram hash"):
        verify_webapp_init_data(urlencode(fields), BOT_TOKEN)


def test_init_data_expired_rejected():
    old = int(time.time()) - 86400 - 10
    with pytest.raises(TelegramHashError, match="expired"):
        verify_webapp_init_data(make_init_data(auth_date=old), BOT_TOKEN)


def test_init_data_future_auth_date_rejected():
    future = int(time.time()) + 3600
    with pytest.raises(TelegramHashError, match="future"):
        verify_webapp_init_data(make_init_data(auth_date=future), BOT_TOKEN)


def test_init_data_missing_hash_rejected():
    with pytest.raises(TelegramHashError, match="Missing hash"):
        verify_webapp_init_data("auth_date=1&user=%7B%22id%22%3A1%7D", BOT_TOKEN)


def test_init_data_bool_user_id_rejected():
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": True})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(TelegramHashError, match="user id"):
        verify_webapp_init_data(urlencode(fields), BOT_TOKEN)


def test_init_data_duplicate_field_rejected():
    raw = make_init_data() + "&auth_date=1"
    with pytest.raises(TelegramHashError, match="Duplicate"):
        verify_webapp_init_data(raw, BOT_TOKEN)


# --- session token ---------------------------------------------------------


def test_session_token_roundtrip():
    token, ttl = issue_session_token(TG_ID)
    assert ttl == settings.miniapp_session_ttl_seconds
    assert verify_session_token(token) == TG_ID


def test_session_token_forged_payload_rejected():
    token, _ = issue_session_token(TG_ID)
    _, sig = token.split(".")
    import base64
    forged_body = base64.urlsafe_b64encode(
        json.dumps({"tg_id": 1, "exp": int(time.time()) + 999}).encode()
    ).rstrip(b"=").decode()
    with pytest.raises(MiniAppSessionError, match="Invalid"):
        verify_session_token(f"{forged_body}.{sig}")


def test_session_token_expired_rejected():
    token, _ = issue_session_token(TG_ID, ttl_seconds=-1)
    with pytest.raises(MiniAppSessionError, match="expired"):
        verify_session_token(token)


@pytest.mark.parametrize("garbage", ["", "nodot", ".", "a.b", "!!.!!"])
def test_session_token_garbage_rejected(garbage):
    with pytest.raises(MiniAppSessionError):
        verify_session_token(garbage)


# --- endpoints -------------------------------------------------------------


@pytest.fixture
def user_repo():
    repo = MagicMock()
    repo.get_by_tg_id = AsyncMock(return_value=None)
    repo.create = AsyncMock(return_value=User(tg_id=TG_ID, username="ivan"))
    return repo


@pytest.fixture
def miniapp_client(api_client, user_repo, mock_service_data):
    app.dependency_overrides[get_user_repository] = lambda: user_repo
    app.dependency_overrides[get_pool] = lambda: AsyncMock()
    yield api_client
    app.dependency_overrides.pop(get_user_repository, None)


@pytest.mark.asyncio
async def test_auth_issues_token_and_registers_new_user(miniapp_client, user_repo):
    r = await miniapp_client.post("/api/v1/miniapp/auth", json={"init_data": make_init_data()})
    assert r.status_code == 200
    body = r.json()
    assert body["tg_id"] == TG_ID
    assert body["is_new"] is True
    assert verify_session_token(body["access_token"]) == TG_ID
    user_repo.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_auth_rejects_bad_signature(miniapp_client, user_repo):
    r = await miniapp_client.post(
        "/api/v1/miniapp/auth", json={"init_data": make_init_data(bot_token="999:OTHER")}
    )
    assert r.status_code == 401
    user_repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_blocked_user_forbidden(miniapp_client, user_repo):
    user_repo.get_by_tg_id = AsyncMock(return_value=User(tg_id=TG_ID, is_blocked=True))
    r = await miniapp_client.post("/api/v1/miniapp/auth", json={"init_data": make_init_data()})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_me_requires_session(miniapp_client):
    r = await miniapp_client.get("/api/v1/miniapp/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_rejects_invalid_token(miniapp_client):
    r = await miniapp_client.get("/api/v1/miniapp/me", headers={"Authorization": "Bearer x.y"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_keys_uses_session_tg_id_only(miniapp_client, mock_service_data):
    mock_service_data.data_service.keys.filter = AsyncMock(return_value=[])
    r = await miniapp_client.get("/api/v1/miniapp/keys?tg_id=999", headers=bearer(TG_ID))
    assert r.status_code == 200
    mock_service_data.data_service.keys.filter.assert_awaited_with(
        mock_service_data.data_service.keys.filter.await_args.args[0], tg_id=TG_ID
    )


@pytest.mark.asyncio
async def test_get_foreign_key_returns_404(miniapp_client, mock_service_data):
    mock_service_data.keys.get_data = AsyncMock(return_value=Key(email="k@x", tg_id=1, expiry_time=0, key="v", client_id="c", inbound_id=1))
    r = await miniapp_client.get("/api/v1/miniapp/keys/k@x", headers=bearer(TG_ID))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_foreign_key_returns_404_and_does_not_touch_panel(miniapp_client, mock_service_data):
    mock_service_data.keys.get_data = AsyncMock(return_value=Key(email="k@x", tg_id=1, expiry_time=0, key="v", client_id="c", inbound_id=1))
    r = await miniapp_client.delete("/api/v1/miniapp/keys/k@x", headers=bearer(TG_ID))
    assert r.status_code == 404
    mock_service_data.data_service.keys.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_renew_payment_for_foreign_key_rejected(miniapp_client, mock_service_data):
    mock_service_data.keys.get_data = AsyncMock(return_value=Key(email="k@x", tg_id=1, expiry_time=0, key="v", client_id="c", inbound_id=1))
    r = await miniapp_client.post(
        "/api/v1/miniapp/payments",
        json={"tariff_id": 2, "operation": "renew_key", "email": "k@x"},
        headers=bearer(TG_ID),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_payment_ignores_client_amount(miniapp_client, mock_service_data, monkeypatch):
    mock_service_data.tariffs.get_data = AsyncMock(return_value=Tariff(id=2, name_tariff="Std", amount=200.0))
    captured = {}

    async def fake_create(body, pool, service_data, cache):
        captured["body"] = body
        return PaymentCreateResponse(
            payment_id="p1", confirmation_url="https://yk", amount=0,
            base_amount=0, volume_discount_percent=0, volume_discount_amount=0,
            referral_discount_amount=0, balance_discount_amount=0, final_amount=0,
            has_volume_discount=False, has_referral_discount=False, has_balance_discount=False,
        )

    import api.v1.miniapp as miniapp_mod
    monkeypatch.setattr(miniapp_mod, "create_payment", fake_create)
    r = await miniapp_client.post(
        "/api/v1/miniapp/payments",
        json={"tariff_id": 2, "number_of_months": 3, "amount": 1, "referral_discount": 99, "tg_id": 1},
        headers=bearer(TG_ID),
    )
    assert r.status_code == 200
    body = captured["body"]
    assert body.tg_id == TG_ID
    assert body.amount is None
    assert body.referral_discount is None


@pytest.mark.asyncio
async def test_create_payment_rejects_free_tariff(miniapp_client, mock_service_data):
    mock_service_data.tariffs.get_data = AsyncMock(return_value=Tariff(id=1, name_tariff="Free", amount=0.0))
    r = await miniapp_client.post(
        "/api/v1/miniapp/payments", json={"tariff_id": 1}, headers=bearer(TG_ID)
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_no_route_accepts_tg_id_for_identity(miniapp_client):
    # Every protected endpoint must reject a request that only passes tg_id.
    r = await miniapp_client.get("/api/v1/miniapp/me?tg_id=%d" % TG_ID)
    assert r.status_code == 401
