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


@pytest.mark.asyncio
async def test_state_new_no_cookie(api_client):
    """Без куки → state: new."""
    response = await api_client.get("/api/v1/landing/state")
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "new"
    # Остальные поля должны быть None
    assert data["key_value"] is None
    assert data["expires_at_ms"] is None


@pytest.mark.asyncio
async def test_state_active_with_valid_cookie(api_client, mock_service_data):
    """С валидной кукой и существующим ключом → state: active."""
    from api.v1.landing import _sign_cookie

    landing_uid = "abc123def456"
    key = make_landing_key(landing_uid=landing_uid)
    # CacheService.keys.all() — async (возвращает coroutine); AsyncMock зеркало
    # реальности. Раньше здесь был sync MagicMock, из-за чего пропадал баг
    # отсутствия await на keys.all() в _get_key_by_landing_uid (regression).
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])
    # _get_key_by_landing_uid смотрит в кеш
    mock_service_data.keys.get_data = AsyncMock(return_value=None)

    cookie = _sign_cookie(landing_uid)

    response = await api_client.get(
        "/api/v1/landing/state",
        cookies={"tg_landing_id": cookie},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["state"] in ("active", "expiring")
    assert data["key_value"] == "vless://test@example.com"
    assert "expires_at_ms" in data
    assert "deep_link_happ" in data
    assert "deep_link_bot" in data
    assert "landing_abc123" in data["deep_link_bot"]
    # vless:// URL встраивается в happ://import/<vless-url> как есть
    assert data["deep_link_happ"].startswith("happ://import/")
    assert "vless://" in data["deep_link_happ"]


@pytest.mark.asyncio
async def test_build_deep_links_happ_vless_plain():
    """Happ deep-link для vless:// конфига — plain vless в happ://import/."""
    from api.v1.landing import _build_deep_links

    vless_url = "vless://uuid@example.com:443?encryption=none&security=tls"
    deep_link_happ, deep_link_bot = _build_deep_links(vless_url, "uid123")

    assert deep_link_happ == f"happ://import/{vless_url}"
    assert deep_link_happ.startswith("happ://import/vless://")
    assert deep_link_bot.startswith("https://t.me/")
    assert "start=landing_uid123" in deep_link_bot


@pytest.mark.asyncio
async def test_build_deep_links_happ_subscription_extracts_vless(monkeypatch):
    """Если subscription URL отдаёт vless, извлекаем и вставляем в happ://import/."""
    from api.v1 import landing as landing_module

    vless_url = "vless://fa5faf41-dc2b-4f26-9802-d520a27c5560@45.8.159.52:443?security=tls&type=tcp"

    def mock_extract(url: str):
        return vless_url

    monkeypatch.setattr(landing_module, "_extract_vless_url", mock_extract)

    from api.v1.landing import _build_deep_links
    subscription_url = "https://tds-pro.space:2096/TolkoDlyaSv0ih_Bot/token123"
    deep_link_happ, _ = _build_deep_links(subscription_url, "uid123")

    assert deep_link_happ == f"happ://import/{vless_url}"


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
async def test_state_invalid_cookie(api_client):
    """С невалидной кукой → state: new (без 401)."""
    response = await api_client.get(
        "/api/v1/landing/state",
        cookies={"tg_landing_id": "invalid.cookie.value"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "new"


@pytest.mark.asyncio
async def test_state_expired_key(api_client, mock_service_data):
    """Ключ с expiry_time в прошлом → state: expired."""
    from api.v1.landing import _sign_cookie
    from models import Key

    landing_uid = "expired_uid"
    expired_key = Key(
        tg_id=-999,
        client_id="uuid-exp",
        email="landing_exp@anon",
        expiry_time=int((time.time() - 3600) * 1000),  # 1ч назад
        key="vless://expired",
        inbound_id=13,
        limit_ip=1,
        landing_uid=landing_uid,
    )
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[expired_key])

    cookie = _sign_cookie(landing_uid)
    response = await api_client.get(
        "/api/v1/landing/state",
        cookies={"tg_landing_id": cookie},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "expired"


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
async def test_claim_new_user(api_client, mock_service_data, monkeypatch):
    """Новый юзер → ключ привязан, апгрейдирован через GraceManager, trial=1, converted."""
    from api.v1 import landing as landing_module

    landing_uid = "claimuid123456"
    key = make_landing_key(email="landing_claim@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])
    mock_service_data.keys.get_data = AsyncMock(return_value=None)

    user = MagicMock(tg_id=999, server_id=2, trial=0)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.keys.update = AsyncMock(return_value=None)
    mock_service_data.users.update = AsyncMock(return_value=None)

    trial_tariff = MagicMock(id=10, name_tariff="trial7", period=7, amount=0.0, limit_ip=1)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=trial_tariff)

    # Патчим GraceManager.upgrade_from_landing → возвращает апгрейднутый ключ
    upgraded = MagicMock()
    upgraded.email = "landing_claim@anon"
    upgraded.key = "vless://test@example.com"
    upgraded.expiry_time = int((time.time() + 7 * 24 * 3600) * 1000)
    upgraded.grace_expiry = upgraded.expiry_time + 7 * 86_400_000
    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock(return_value=upgraded)
    monkeypatch.setattr(
        landing_module, "build_grace_manager",
        lambda *a, **k: grace,
    )
    # Патчим TrialService, чтобы не шёл в реальную БД
    monkeypatch.setattr(
        landing_module.TrialService, "installation_trial",
        AsyncMock(return_value=user),
    )

    cache = _mock_cache()
    _override_cache(cache)

    resp = await api_client.post(
        f"/api/v1/landing/claim/{landing_uid}", json={"tg_id": 999}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "claimed"
    assert data["email"] == "landing_claim@anon"
    assert data["key_value"] == "vless://test@example.com"

    # converted_tg_id выставляется до вызова upgrade (идемпотентность повторных кликов)
    assert key.converted_tg_id == 999
    # Срок из апгрейднутого ключа
    assert data["expires_at_ms"] > int((time.time() + 24 * 3600) * 1000)
    grace.upgrade_from_landing.assert_awaited_once()
    mock_service_data.users.update.assert_awaited_once()  # server_id 2→1


@pytest.mark.asyncio
async def test_claim_already_same_user(api_client, mock_service_data, monkeypatch):
    """Повторный claim тем же юзером → идемпотентный already_claimed, без extend."""
    from api.v1 import landing as landing_module

    landing_uid = "claimuid_already"
    key = make_landing_key(email="landing_al@anon", landing_uid=landing_uid)
    key.tg_id = 999
    key.converted_tg_id = 999
    key.expiry_time = int((time.time() + 7 * 24 * 3600) * 1000)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    mock_xui = MagicMock()
    mock_xui.extend_client_key = AsyncMock(return_value=True)
    monkeypatch.setattr(
        landing_module, "build_key_services",
        lambda *a, **k: (None, None, mock_xui),
    )
    _override_cache(_mock_cache())

    resp = await api_client.post(
        f"/api/v1/landing/claim/{landing_uid}", json={"tg_id": 999}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "already_claimed"
    assert data["email"] == "landing_al@anon"
    mock_xui.extend_client_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_already_other(api_client, mock_service_data, monkeypatch):
    """Ключ уже привязан к другому аккаунту → already_claimed_other, без extend."""
    from api.v1 import landing as landing_module

    landing_uid = "claimuid_other"
    key = make_landing_key(email="landing_ot@anon", landing_uid=landing_uid)
    key.converted_tg_id = 888  # другой аккаунт
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    mock_xui = MagicMock()
    mock_xui.extend_client_key = AsyncMock(return_value=True)
    monkeypatch.setattr(
        landing_module, "build_key_services",
        lambda *a, **k: (None, None, mock_xui),
    )
    _override_cache(_mock_cache())

    resp = await api_client.post(
        f"/api/v1/landing/claim/{landing_uid}", json={"tg_id": 999}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "already_claimed_other"
    mock_xui.extend_client_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_user_not_found(api_client, mock_service_data, monkeypatch):
    """Юзер не зарегистрирован (бот не вызвал авто-регистрацию) → 404."""
    landing_uid = "claimuid_nouser"
    key = make_landing_key(email="landing_no@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])
    mock_service_data.users.get_data = AsyncMock(return_value=None)

    resp = await api_client.post(
        f"/api/v1/landing/claim/{landing_uid}", json={"tg_id": 999}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_claim_key_not_found(api_client, mock_service_data):
    """landing_uid не существует → 404."""
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[])
    resp = await api_client.post(
        "/api/v1/landing/claim/unknown_uid", json={"tg_id": 999}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_claim_panel_extend_fails(api_client, mock_service_data, monkeypatch):
    """upgrade_from_landing вернул None → 500, converted_tg_id откатывается."""
    from api.v1 import landing as landing_module

    landing_uid = "claimuid_xuifail"
    key = make_landing_key(email="landing_xui@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    user = MagicMock(tg_id=999, server_id=2, trial=0)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    trial_tariff = MagicMock(id=10, name_tariff="trial7", period=7, amount=0.0, limit_ip=1)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=trial_tariff)

    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock(return_value=None)
    monkeypatch.setattr(
        landing_module, "build_grace_manager",
        lambda *a, **k: grace,
    )
    _override_cache(_mock_cache())

    resp = await api_client.post(
        f"/api/v1/landing/claim/{landing_uid}", json={"tg_id": 999}
    )
    assert resp.status_code == 500
    # При провале upgrade converted_tg_id откатывается, чтобы повторный клик
    # мог retry (иначе already_claimed без рабочего ключа).
    assert key.converted_tg_id is None


# =============================================================================
# POST /landing/mark-converted/{landing_uid}
# =============================================================================

@pytest.mark.asyncio
async def test_mark_converted_already_other_no_overwrite(api_client, mock_service_data):
    """Ключ уже привязан к другому аккаунту → already_claimed_other, без keys.update."""
    landing_uid = "mc_other"
    key = make_landing_key(email="mc_other@anon", landing_uid=landing_uid)
    key.converted_tg_id = 888
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])
    mock_service_data.keys.update = AsyncMock(return_value=None)
    _override_cache(_mock_cache())

    resp = await api_client.post(
        f"/api/v1/landing/mark-converted/{landing_uid}", json={"tg_id": 999}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "already_claimed_other"
    # Ключ НЕ перезаписан
    assert key.converted_tg_id == 888
    mock_service_data.keys.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_converted_idempotent(api_client, mock_service_data):
    """Тот же юзер повторно → already=True, без keys.update."""
    landing_uid = "mc_idem"
    key = make_landing_key(email="mc_idem@anon", landing_uid=landing_uid)
    key.converted_tg_id = 999
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])
    mock_service_data.keys.update = AsyncMock(return_value=None)
    _override_cache(_mock_cache())

    resp = await api_client.post(
        f"/api/v1/landing/mark-converted/{landing_uid}", json={"tg_id": 999}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["already"] is True
    mock_service_data.keys.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_converted_new(api_client, mock_service_data):
    """Свободный ключ → converted_tg_id выставлен, keys.update вызван."""
    landing_uid = "mc_new"
    key = make_landing_key(email="mc_new@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])
    mock_service_data.keys.update = AsyncMock(return_value=None)
    cache = _mock_cache()
    _override_cache(cache)

    resp = await api_client.post(
        f"/api/v1/landing/mark-converted/{landing_uid}", json={"tg_id": 999}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert key.converted_tg_id == 999
    mock_service_data.keys.update.assert_awaited_once()
    cache.keys.set.assert_awaited_once()


# =============================================================================
# GET /landing/state — ветка converted
# =============================================================================

@pytest.mark.asyncio
async def test_state_converted_after_claim(api_client, mock_service_data):
    """Ключ с converted_tg_id и реальным tg_id (>0) → state: converted."""
    from api.v1.landing import _sign_cookie
    from models import Key

    landing_uid = "conv_uid"
    key = Key(
        tg_id=999,  # реальный (claim перенёс)
        client_id="uuid-conv",
        email="conv@anon",
        expiry_time=int((time.time() + 7 * 24 * 3600) * 1000),
        key="vless://conv",
        inbound_id=13,
        limit_ip=1,
        landing_uid=landing_uid,
        converted_tg_id=999,
    )
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    cookie = _sign_cookie(landing_uid)
    resp = await api_client.get(
        "/api/v1/landing/state", cookies={"tg_landing_id": cookie}
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "converted"


@pytest.mark.asyncio
async def test_state_active_after_mark_converted_only(api_client, mock_service_data):
    """converted_tg_id выставлен, но ключ остался на псевдо-tg_id (<0) —
    mark-converted для существующего юзера. Лендинг продолжает показывать
    активный 24ч ключ (не converted)."""
    from api.v1.landing import _sign_cookie

    landing_uid = "mc_state"
    key = make_landing_key(email="mc_state@anon", landing_uid=landing_uid)
    key.converted_tg_id = 999  # mark-converted, но tg_id остался псевдо (<0)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    cookie = _sign_cookie(landing_uid)
    resp = await api_client.get(
        "/api/v1/landing/state", cookies={"tg_landing_id": cookie}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] in ("active", "expiring")
    assert data["key_value"] == "vless://test@example.com"


@pytest.mark.asyncio
async def test_state_already_registered_after_mark_converted(api_client, mock_service_data):
    """mark-converted (converted_tg_id set, tg_id<0), ключ жив → already_registered=True + bot_url."""
    from api.v1.landing import _sign_cookie

    landing_uid = "ar_state"
    key = make_landing_key(email="ar_state@anon", landing_uid=landing_uid)
    key.converted_tg_id = 999  # mark-converted, tg_id остался псевдо (<0)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    cookie = _sign_cookie(landing_uid)
    resp = await api_client.get(
        "/api/v1/landing/state", cookies={"tg_landing_id": cookie}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] in ("active", "expiring")
    assert data["already_registered"] is True
    assert data["key_value"] == "vless://test@example.com"
    assert data["bot_url"].startswith("https://t.me/")
    assert "start=landing_" not in data["bot_url"]


@pytest.mark.asyncio
async def test_state_fresh_key_not_already_registered(api_client, mock_service_data):
    """Свежий ключ (converted_tg_id=None), жив → already_registered=False, bot_url есть."""
    from api.v1.landing import _sign_cookie

    landing_uid = "fresh_state"
    key = make_landing_key(email="fresh_state@anon", landing_uid=landing_uid)
    # converted_tg_id не выставлен
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    cookie = _sign_cookie(landing_uid)
    resp = await api_client.get(
        "/api/v1/landing/state", cookies={"tg_landing_id": cookie}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] in ("active", "expiring")
    assert data["already_registered"] is False
    assert data["bot_url"].startswith("https://t.me/")


@pytest.mark.asyncio
async def test_state_expired_converted_no_already_registered(api_client, mock_service_data):
    """Истёкший ключ с converted_tg_id (tg_id<0) → expired, already_registered=False."""
    from api.v1.landing import _sign_cookie
    from models import Key

    landing_uid = "ar_exp"
    key = Key(
        tg_id=-999,
        client_id="uuid-are",
        email="ar_exp@anon",
        expiry_time=int((time.time() - 3600) * 1000),  # 1ч назад — истёк
        key="vless://ar",
        inbound_id=13,
        limit_ip=1,
        landing_uid=landing_uid,
        converted_tg_id=999,
    )
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    cookie = _sign_cookie(landing_uid)
    resp = await api_client.get(
        "/api/v1/landing/state", cookies={"tg_landing_id": cookie}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["state"] == "expired"
    assert data["already_registered"] is False
