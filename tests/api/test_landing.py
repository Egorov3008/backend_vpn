"""
Smoke-тесты для /api/v1/landing/*.

Полное покрытие (с реальным 3x-UI и БД) — после деплоя на dev-стенд.
Здесь проверяем, что эндпоинты существуют, отвечают и не падают на 500.
"""
import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.dependencies import get_cache
from app.main import app


def make_landing_key(email="landing_abc@anon", landing_uid="abc123"):
    """Анонимный ключ с лендинга."""
    from models import Key
    return Key(
        tg_id=-123456789,
        client_id="uuid-test",
        email=email,
        expiry_time=int((time.time() + 24 * 3600) * 1000),  # 24ч от now
        key="vless://test@example.com",
        inbound_id=13,
        limit_ip=1,
        landing_uid=landing_uid,
    )


def _mock_cache():
    """Cache-объект с async-методами для write-путей (claim/mark-converted)."""
    cache = MagicMock()
    cache.keys.set = AsyncMock(return_value=None)
    cache.users.set = AsyncMock(return_value=None)
    return cache


def _override_cache(cache):
    """Переопределить зависимость get_cache (в api_client она = пустой MagicMock)."""
    app.dependency_overrides[get_cache] = lambda: cache


def _pool_with_fetchrow(fetchrow_return):
    """asyncpg.Pool mock: pool.acquire() → conn с заданным fetchrow.

    Нужен для #8 — _attach_referral_if_any теперь делает atomic
    ``UPDATE ... RETURNING`` через ``async with pool.acquire() as conn``,
    а ``AsyncMock()`` не выставляет ``__aenter__``/``__aexit__``.
    Возвращает (pool, conn).
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _override_pool(pool):
    """Переопределить зависимость get_pool для end-to-end тестов claim/mark."""
    from app.dependencies import get_pool
    app.dependency_overrides[get_pool] = lambda: pool


@pytest.mark.asyncio
async def test_build_deep_links_happ_vless_plain(monkeypatch):
    """Happ deep-link для одиночного vless://-конфига → happ://add/<plain>."""
    from api.v1.landing import _build_deep_links
    from bot_project import bot as telegram_bot

    # Не бьём в реальный Telegram getMe — username закеширован заранее.
    monkeypatch.setattr(telegram_bot, "_username_cache", "test_bot")

    vless_url = "vless://uuid@example.com:443?encryption=none&security=tls"
    deep_link_happ, deep_link_bot = await _build_deep_links(vless_url, "uid123")

    assert deep_link_happ == f"happ://add/{vless_url}"
    assert deep_link_happ.startswith("happ://add/vless://")
    assert deep_link_bot.startswith("https://t.me/")
    assert "start=landing_uid123" in deep_link_bot


@pytest.mark.asyncio
async def test_extract_vless_url_decodes_base64():
    """_extract_vless_url декодирует base64-encoded subscription."""
    import base64
    from api.v1.landing import _extract_vless_url

    vless_url = "vless://uuid@example.com:443?security=tls"
    encoded = base64.b64encode(f"{vless_url}\n".encode()).decode()

    class FakeResponse:
        def read(self):
            return encoded.encode()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    # monkeypatch urllib.request.urlopen
    import urllib.request
    original_urlopen = urllib.request.urlopen

    def fake_urlopen(url, timeout=None):
        return FakeResponse()

    urllib.request.urlopen = fake_urlopen
    try:
        result = _extract_vless_url("https://example.com/sub")
        assert result == vless_url
    finally:
        urllib.request.urlopen = original_urlopen


@pytest.mark.asyncio
async def test_cookie_sign_and_verify():
    """HMAC-подпись куки: sign → verify возвращает тот же uid."""
    from api.v1.landing import _sign_cookie, _verify_cookie

    uid = "test_uid_16chars"
    cookie = _sign_cookie(uid)
    assert "." in cookie
    assert _verify_cookie(cookie) == uid


@pytest.mark.asyncio
async def test_cookie_verify_rejects_tampering():
    """Подделанная кука → verify возвращает None."""
    from api.v1.landing import _sign_cookie, _verify_cookie

    uid = "test_uid_16chars"
    cookie = _sign_cookie(uid)
    # Подменим последний символ подписи
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    assert _verify_cookie(tampered) is None


@pytest.mark.asyncio
async def test_pseudo_tg_id_is_negative_and_deterministic():
    """_pseudo_tg_id всегда отрицательный и детерминированный."""
    from api.v1.landing import _pseudo_tg_id

    uid1 = "test_uid_1"
    uid2 = "test_uid_2"
    assert _pseudo_tg_id(uid1) < 0
    assert _pseudo_tg_id(uid2) < 0
    assert _pseudo_tg_id(uid1) != _pseudo_tg_id(uid2)
    # Детерминизм
    assert _pseudo_tg_id(uid1) == _pseudo_tg_id(uid1)


# =============================================================================
# POST /landing/claim/{landing_uid}
# =============================================================================

@pytest.mark.asyncio
async def test_set_ref_cookie_with_invalid_token_returns_400(api_client):
    """Невалидный формат ref_token → 400, без куки."""
    resp = await api_client.post(
        "/api/v1/landing/set-ref-cookie",
        json={"ref_token": "ref_X"},  # короткий
    )
    assert resp.status_code == 400

    resp2 = await api_client.post(
        "/api/v1/landing/set-ref-cookie",
        json={"ref_token": "foo_bar"},
    )
    assert resp2.status_code == 400

    resp3 = await api_client.post(
        "/api/v1/landing/set-ref-cookie",
        json={"ref_token": ""},
    )
    assert resp3.status_code == 400


@pytest.mark.asyncio
async def test_set_ref_cookie_with_unknown_token_returns_404(api_client, mock_service_data):
    """Формат валидный, но записи в БД нет → 404, без куки."""
    mock_service_data.referral_links.get_by = AsyncMock(return_value=None)

    resp = await api_client.post(
        "/api/v1/landing/set-ref-cookie",
        json={"ref_token": "ref_aabbcc112233"},  # валидный 12hex
    )
    assert resp.status_code == 404
    # Нет Set-Cookie при ошибке
    assert "tg_ref" not in resp.headers.get("set-cookie", "")


# =============================================================================
# HMAC-кука tg_ref: sign / verify round-trip
# =============================================================================

@pytest.mark.asyncio
async def test_ref_cookie_sign_and_verify():
    """sign → verify возвращает тот же ref_token."""
    from api.v1.landing import _sign_ref_cookie, _verify_ref_cookie

    token = "ref_abcdef123456"
    cookie = _sign_ref_cookie(token)
    assert "." in cookie
    assert _verify_ref_cookie(cookie) == token


@pytest.mark.asyncio
async def test_ref_cookie_verify_rejects_tampering():
    """Подделанная подпись → None."""
    from api.v1.landing import _sign_ref_cookie, _verify_ref_cookie

    token = "ref_abcdef123456"
    cookie = _sign_ref_cookie(token)
    # Подменим последний символ подписи
    tampered = cookie[:-1] + ("0" if cookie[-1] != "0" else "1")
    assert _verify_ref_cookie(tampered) is None


@pytest.mark.asyncio
async def test_ref_cookie_verify_rejects_garbage():
    """Мусор в куке → None."""
    from api.v1.landing import _verify_ref_cookie

    assert _verify_ref_cookie("") is None
    assert _verify_ref_cookie(None) is None  # type: ignore[arg-type]
    assert _verify_ref_cookie("invalid") is None
    assert _verify_ref_cookie("foo.bar") is None
    # Объекты, приводимые к строке (например, Starlette Cookie), не падают.
    class WeirdCookie:
        def __str__(self):
            return "weird.value"
    assert _verify_ref_cookie(WeirdCookie()) is None


@pytest.mark.asyncio
async def test_ref_cookie_is_distinct_from_landing_cookie():
    """Подписи tg_ref и tg_landing_id НЕ совпадают (разные payload)."""
    from api.v1.landing import _sign_cookie, _sign_ref_cookie, _verify_cookie, _verify_ref_cookie

    ref_token = "ref_abcdef123456"
    landing_uid = "abc123def456"

    ref_cookie = _sign_ref_cookie(ref_token)
    landing_cookie = _sign_cookie(landing_uid)

    # tg_ref декодируется только _verify_ref_cookie
    assert _verify_ref_cookie(ref_cookie) == ref_token
    assert _verify_cookie(ref_cookie) is None
    # И наоборот
    assert _verify_cookie(landing_cookie) == landing_uid
    assert _verify_ref_cookie(landing_cookie) is None


# =============================================================================
# _is_valid_ref_format — regex-проверка
# =============================================================================

def test_is_valid_ref_format():
    """Валидация формата ref_<12hex>."""
    from api.v1.landing import _is_valid_ref_format

    # Валидные
    assert _is_valid_ref_format("ref_abcdef123456") is True
    assert _is_valid_ref_format("ref_0123456789ab") is True

    # Невалидные
    assert _is_valid_ref_format("") is False
    assert _is_valid_ref_format(None) is False  # type: ignore[arg-type]
    assert _is_valid_ref_format("ref_xyz") is False  # короткий
    assert _is_valid_ref_format("ref_toolong123456") is False  # длинный
    assert _is_valid_ref_format("gift_abcdef123456") is False  # неверный префикс
    assert _is_valid_ref_format("abcdef123456") is False  # без префикса
    assert _is_valid_ref_format("ref_ABCDEFGHIJKL") is False  # не-hex (regex lowercase-only)
    assert _is_valid_ref_format("ref_ABCDEF123456") is False  # uppercase не проходит regex


# =============================================================================
# _attach_referral_if_any — merge в claim и mark_converted
# =============================================================================

# ---------------------------------------------------------------------------
# Доступ к claim / mark-converted: нужен X-Landing-Internal-Secret.
# nginx подставляет X-Bot-Secret на публичных путях /api/v1/landing/*, поэтому
# один только bot-secret не должен открывать эти маршруты.
# ---------------------------------------------------------------------------
_INTERNAL_SECRET = "internal-secret-for-tests-32chars!!"


@pytest.fixture
def real_internal_secret_check(api_client, monkeypatch):
    """Снять заглушку из conftest, чтобы отработала настоящая проверка секрета."""
    from config import settings
    from app.auth import verify_landing_internal_secret

    app.dependency_overrides.pop(verify_landing_internal_secret, None)
    monkeypatch.setattr(settings, "landing_internal_secret", _INTERNAL_SECRET)
    return api_client


@pytest.mark.asyncio
async def test_claim_rejected_without_internal_secret(real_internal_secret_check):
    """Публичный запрос с одним только bot-secret (его подставляет nginx) → 401."""
    resp = await real_internal_secret_check.post(
        "/api/v1/landing/claim/anyuid", json={"tg_id": 999}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mark_converted_rejected_with_wrong_internal_secret(real_internal_secret_check):
    resp = await real_internal_secret_check.post(
        "/api/v1/landing/mark-converted/anyuid",
        json={"tg_id": 999},
        headers={"X-Landing-Internal-Secret": "wrong"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_routes_closed_when_internal_secret_not_configured(real_internal_secret_check, monkeypatch):
    """Пустой LANDING_INTERNAL_SECRET = маршруты закрыты, даже если заголовок передан."""
    from config import settings

    monkeypatch.setattr(settings, "landing_internal_secret", "")
    resp = await real_internal_secret_check.post(
        "/api/v1/landing/claim/anyuid",
        json={"tg_id": 999},
        headers={"X-Landing-Internal-Secret": ""},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_claim_passes_auth_with_correct_internal_secret(real_internal_secret_check, mock_service_data):
    """С верным секретом запрос доходит до обработчика (здесь — 404: ключа нет)."""
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[])
    resp = await real_internal_secret_check.post(
        "/api/v1/landing/claim/unknown_uid",
        json={"tg_id": 999},
        headers={"X-Landing-Internal-Secret": _INTERNAL_SECRET},
    )
    assert resp.status_code == 404
