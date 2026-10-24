"""Реферальная ссылка ведёт на сайт, и друг, пришедший по ней, засчитывается при входе через бота."""
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.integration.conftest import LANDING_INTERNAL_SECRET
from tests.integration.helpers import db_user, register_user

SITE = "https://site.example"
WEB_HEADERS = {"X-Requested-With": "web"}
BOT_CONFIRM_HEADERS = {"X-Landing-Internal-Secret": LANDING_INTERNAL_SECRET}


@pytest.fixture
def site(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "web_public_url", f"{SITE}/")
    # Тестовый клиент ходит по http: Secure-куки он бы не отправил обратно.
    monkeypatch.setattr(settings, "web_cookie_secure", False)


def _ref_from(share_url: str) -> str:
    parts = urlsplit(share_url)
    assert f"{parts.scheme}://{parts.netloc}" == SITE
    return parse_qs(parts.query)["ref"][0]


async def test_share_url_points_to_site_and_is_stable(backend, site):
    await register_user(backend, 4001)

    first = await backend.client.post("/api/v1/admin/referrals/links", params={"tg_id": 4001})
    again = await backend.client.get("/api/v1/admin/referrals/links/4001")

    assert first.status_code == 200 and again.status_code == 200
    token = first.json()["token"]
    assert _ref_from(first.json()["share_url"]) == token
    assert again.json()["share_url"] == first.json()["share_url"]
    by_token = await backend.client.get(f"/api/v1/admin/referrals/links/by-token/{token}")
    assert by_token.json()["share_url"] == first.json()["share_url"]
    rows = await backend.pool.fetchval("SELECT count(*) FROM referral_links WHERE referrer_tg_id = 4001")
    assert rows == 1


async def test_no_share_url_without_site_address(backend, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "web_public_url", "")
    await register_user(backend, 4002)

    resp = await backend.client.post("/api/v1/admin/referrals/links", params={"tg_id": 4002})

    assert resp.status_code == 200
    assert resp.json()["token"] and resp.json()["share_url"] is None


async def test_friend_from_share_url_becomes_referral_after_bot_login(backend, site):
    await register_user(backend, 4010)
    link = (await backend.client.post("/api/v1/admin/referrals/links", params={"tg_id": 4010})).json()

    # Друг открыл ссылку на сайте: сайт отправляет ?ref=… на бэкенд.
    ref = await backend.client.post(
        "/api/v1/web/anonymous/ref", json={"ref_token": _ref_from(link["share_url"])}, headers=WEB_HEADERS
    )
    assert ref.status_code == 204
    # «Войти через бота»: бот регистрирует нового пользователя и подтверждает вход.
    start = await backend.client.post("/api/v1/web/auth/bot-login", headers=WEB_HEADERS)
    assert start.status_code == 200, start.text
    login_token = start.json()["deep_link"].rsplit("weblogin_", 1)[1]
    await register_user(backend, 4011)
    confirm = await backend.client.post(
        f"/api/v1/auth/web-login/{login_token}/confirm", json={"tg_id": 4011}, headers=BOT_CONFIRM_HEADERS
    )
    assert confirm.json()["status"] == "confirmed"

    done = await backend.client.post("/api/v1/web/auth/bot-login/complete", headers=WEB_HEADERS)

    assert done.json()["status"] == "confirmed"
    assert (await db_user(backend, 4011))["referral_id"] == 4010
    redemptions = await backend.pool.fetchval(
        "SELECT count(*) FROM referral_redemptions WHERE referred_tg_id = 4011"
    )
    assert redemptions == 1


async def test_cabinet_returns_site_share_url(backend, site):
    from app.core.miniapp_session import web_sessions

    await register_user(backend, 4020)
    token, _ = web_sessions.issue(4020)

    resp = await backend.client.post(
        "/api/v1/web/referral-link", headers={**WEB_HEADERS, "Cookie": f"web_session={token}"}
    )

    assert resp.status_code == 200, resp.text
    assert _ref_from(resp.json()["share_url"]) == resp.json()["token"]
