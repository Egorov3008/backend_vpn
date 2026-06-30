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


# =============================================================================
# POST /landing/set-ref-cookie — HMAC tg_ref cookie для реферера с лендинга
# =============================================================================

@pytest.mark.asyncio
async def test_set_ref_cookie_with_valid_token(api_client, mock_service_data):
    """Валидный ref_token и запись в БД → 200, Set-Cookie: tg_ref=…."""
    from api.v1.landing import TG_REF_COOKIE_NAME
    from models.referrals.referral_link import ReferralLink

    link = ReferralLink(id=1, referrer_tg_id=42, token="ref_abcdef123456")
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    resp = await api_client.post(
        "/api/v1/landing/set-ref-cookie",
        json={"ref_token": "ref_abcdef123456"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}

    # Set-Cookie должен содержать tg_ref с HMAC-подписью
    set_cookie = resp.headers.get("set-cookie", "")
    assert TG_REF_COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


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

@pytest.mark.asyncio
async def test_attach_referral_merges_users_referral_id(api_client, mock_service_data):
    """claim с валидной tg_ref-кукой → users.referral_id записан, redemption создан."""
    from api.v1.landing import _attach_referral_if_any, _sign_ref_cookie
    from models.referrals.referral_link import ReferralLink

    ref_token = "ref_abcdef123456"
    referrer_tg_id = 100
    referred_tg_id = 999
    link = ReferralLink(id=5, referrer_tg_id=referrer_tg_id, token=ref_token)
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    user = MagicMock(tg_id=referred_tg_id, server_id=2, trial=0, referral_id=None)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    cache = _mock_cache()
    pool = AsyncMock()

    cookie = _sign_ref_cookie(ref_token)
    result = await _attach_referral_if_any(pool, mock_service_data, cache, cookie, referred_tg_id)

    assert result is True
    assert user.referral_id == referrer_tg_id
    mock_service_data.users.update.assert_awaited_once()
    cache.users.set.assert_awaited_once()
    mock_service_data.data_service.referral_redemptions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_attach_referral_self_referral_blocked(api_client, mock_service_data):
    """referrer == referred → молча skip, без записи в users и redemption."""
    from api.v1.landing import _attach_referral_if_any, _sign_ref_cookie
    from models.referrals.referral_link import ReferralLink

    ref_token = "ref_abcdef123456"
    same_id = 100
    link = ReferralLink(id=5, referrer_tg_id=same_id, token=ref_token)
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    user = MagicMock(tg_id=same_id, referral_id=None)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    cache = _mock_cache()
    pool = AsyncMock()

    cookie = _sign_ref_cookie(ref_token)
    result = await _attach_referral_if_any(pool, mock_service_data, cache, cookie, same_id)

    assert result is False
    mock_service_data.users.update.assert_not_awaited()
    cache.users.set.assert_not_awaited()
    mock_service_data.data_service.referral_redemptions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_referral_does_not_overwrite_existing(api_client, mock_service_data):
    """У юзера уже есть referral_id (старая t.me-ветка) → НЕ перезаписываем."""
    from api.v1.landing import _attach_referral_if_any, _sign_ref_cookie
    from models.referrals.referral_link import ReferralLink

    ref_token = "ref_newref1234"
    new_referrer = 100
    existing_referrer = 200
    link = ReferralLink(id=5, referrer_tg_id=new_referrer, token=ref_token)
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    user = MagicMock(tg_id=999, referral_id=existing_referrer)  # уже есть
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    cache = _mock_cache()
    pool = AsyncMock()

    cookie = _sign_ref_cookie(ref_token)
    result = await _attach_referral_if_any(pool, mock_service_data, cache, cookie, 999)

    assert result is False
    assert user.referral_id == existing_referrer  # не изменился
    mock_service_data.users.update.assert_not_awaited()
    mock_service_data.data_service.referral_redemptions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_referral_invalid_cookie_no_op(api_client, mock_service_data):
    """Невалидная/просроченная/чужая кука → молча skip."""
    from api.v1.landing import _attach_referral_if_any

    cache = _mock_cache()
    pool = AsyncMock()

    # Пустая кука
    assert await _attach_referral_if_any(pool, mock_service_data, cache, None, 999) is False
    assert await _attach_referral_if_any(pool, mock_service_data, cache, "", 999) is False
    # Мусор
    assert await _attach_referral_if_any(pool, mock_service_data, cache, "garbage", 999) is False
    assert await _attach_referral_if_any(pool, mock_service_data, cache, "foo.bar", 999) is False
    # Подделанная подпись
    assert await _attach_referral_if_any(
        pool, mock_service_data, cache, "eyJhY2I.signature_bad", 999
    ) is False

    mock_service_data.users.update.assert_not_awaited()
    mock_service_data.referral_links.get_by.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_referral_token_not_in_db(api_client, mock_service_data):
    """Формат куки валидный, но токена в БД нет → skip."""
    from api.v1.landing import _attach_referral_if_any, _sign_ref_cookie

    mock_service_data.referral_links.get_by = AsyncMock(return_value=None)
    cache = _mock_cache()
    pool = AsyncMock()

    cookie = _sign_ref_cookie("ref_unknown0000")
    result = await _attach_referral_if_any(pool, mock_service_data, cache, cookie, 999)

    assert result is False
    mock_service_data.users.update.assert_not_awaited()
    mock_service_data.data_service.referral_redemptions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_referral_unique_violation_idempotent(api_client, mock_service_data):
    """Duplicate redemption (UNIQUE constraint) — идемпотентно, не падает."""
    import asyncpg
    from api.v1.landing import _attach_referral_if_any, _sign_ref_cookie
    from models.referrals.referral_link import ReferralLink

    ref_token = "ref_abcdef123456"
    link = ReferralLink(id=5, referrer_tg_id=100, token=ref_token)
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    user = MagicMock(tg_id=999, referral_id=None)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.data_service.referral_redemptions.create = AsyncMock(
        side_effect=asyncpg.UniqueViolationError("duplicate key")
    )
    cache = _mock_cache()
    pool = AsyncMock()

    cookie = _sign_ref_cookie(ref_token)
    # Не должно бросить — UniqueViolationError ловится
    result = await _attach_referral_if_any(pool, mock_service_data, cache, cookie, 999)

    # Merge всё равно засчитан (referral_id записан, redemption idempotent)
    assert result is True
    assert user.referral_id == 100
    mock_service_data.users.update.assert_awaited_once()
    cache.users.set.assert_awaited_once()


# =============================================================================
# claim_key / mark_converted — end-to-end с tg_ref-кукой
# =============================================================================

@pytest.mark.asyncio
async def test_claim_triggers_attach_referral(
    api_client, mock_service_data, monkeypatch
):
    """POST /claim с tg_ref-кукой → claim успешен + merge вызван."""
    from api.v1.landing import _sign_ref_cookie
    from models.referrals.referral_link import ReferralLink

    landing_uid = "claim_with_ref"
    key = make_landing_key(email="claim_ref@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    user = MagicMock(tg_id=999, server_id=2, trial=0, referral_id=None)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.keys.update = AsyncMock(return_value=None)
    mock_service_data.users.update = AsyncMock(return_value=None)

    trial_tariff = MagicMock(id=10, name_tariff="trial7", period=7, amount=0.0, limit_ip=1)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=trial_tariff)

    # Patch GraceManager.upgrade_from_landing
    from api.v1 import landing as landing_module
    upgraded = MagicMock()
    upgraded.email = "claim_ref@anon"
    upgraded.key = "vless://test@example.com"
    upgraded.expiry_time = int((time.time() + 7 * 24 * 3600) * 1000)
    upgraded.grace_expiry = upgraded.expiry_time + 7 * 86_400_000
    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock(return_value=upgraded)
    monkeypatch.setattr(
        landing_module, "build_grace_manager", lambda *a, **k: grace
    )
    monkeypatch.setattr(
        landing_module.TrialService, "installation_trial",
        AsyncMock(return_value=user),
    )

    # Referral link для merge
    ref_token = "ref_abcdef123456"
    link = ReferralLink(id=5, referrer_tg_id=100, token=ref_token)
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    cache = _mock_cache()
    _override_cache(cache)

    cookie = _sign_ref_cookie(ref_token)
    resp = await api_client.post(
        f"/api/v1/landing/claim/{landing_uid}",
        json={"tg_id": 999},
        cookies={"tg_ref": cookie},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "claimed"

    # Merge выполнен: referral_id записан, redemption создан
    assert user.referral_id == 100
    mock_service_data.data_service.referral_redemptions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_converted_triggers_attach_referral(
    api_client, mock_service_data
):
    """POST /mark-converted с tg_ref-кукой → merge вызван."""
    from api.v1.landing import _sign_ref_cookie
    from models.referrals.referral_link import ReferralLink

    landing_uid = "mc_with_ref"
    key = make_landing_key(email="mc_ref@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])
    mock_service_data.keys.update = AsyncMock(return_value=None)

    user = MagicMock(tg_id=999, server_id=2, trial=0, referral_id=None)
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    ref_token = "ref_abcdef123456"
    link = ReferralLink(id=5, referrer_tg_id=100, token=ref_token)
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    cache = _mock_cache()
    _override_cache(cache)

    cookie = _sign_ref_cookie(ref_token)
    resp = await api_client.post(
        f"/api/v1/landing/mark-converted/{landing_uid}",
        json={"tg_id": 999},
        cookies={"tg_ref": cookie},
    )
    assert resp.status_code == 200, resp.text

    # Merge выполнен
    assert user.referral_id == 100
    mock_service_data.data_service.referral_redemptions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_without_cookie_no_merge(api_client, mock_service_data, monkeypatch):
    """POST /claim БЕЗ tg_ref-куки → merge НЕ вызывается, claim работает."""
    from api.v1 import landing as landing_module

    landing_uid = "claim_no_ref"
    key = make_landing_key(email="claim_noref@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    user = MagicMock(tg_id=999, server_id=2, trial=0, referral_id=None)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.keys.update = AsyncMock(return_value=None)
    mock_service_data.users.update = AsyncMock(return_value=None)

    trial_tariff = MagicMock(id=10, name_tariff="trial7", period=7, amount=0.0, limit_ip=1)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=trial_tariff)

    upgraded = MagicMock()
    upgraded.email = "claim_noref@anon"
    upgraded.key = "vless://test@example.com"
    upgraded.expiry_time = int((time.time() + 7 * 24 * 3600) * 1000)
    upgraded.grace_expiry = upgraded.expiry_time + 7 * 86_400_000
    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock(return_value=upgraded)
    monkeypatch.setattr(
        landing_module, "build_grace_manager", lambda *a, **k: grace
    )
    monkeypatch.setattr(
        landing_module.TrialService, "installation_trial",
        AsyncMock(return_value=user),
    )

    cache = _mock_cache()
    _override_cache(cache)

    resp = await api_client.post(
        f"/api/v1/landing/claim/{landing_uid}",
        json={"tg_id": 999},
        # БЕЗ cookies=tg_ref
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "claimed"

    # Без tg_ref referral_links.get_by НЕ вызывается
    mock_service_data.referral_links.get_by.assert_not_awaited()
    mock_service_data.data_service.referral_redemptions.create.assert_not_awaited()
    # users.update был вызван ТОЛЬКО для server_id (без referral_id)
    assert user.referral_id is None


@pytest.mark.asyncio
async def test_claim_with_ref_token_in_body_triggers_merge(
    api_client, mock_service_data, monkeypatch
):
    """POST /claim с ref_token в теле (deeplink) → merge вызван без куки."""
    from models.referrals.referral_link import ReferralLink

    landing_uid = "claim_ref_body"
    key = make_landing_key(email="claim_ref_body@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    user = MagicMock(tg_id=999, server_id=2, trial=0, referral_id=None)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.keys.update = AsyncMock(return_value=None)
    mock_service_data.users.update = AsyncMock(return_value=None)

    trial_tariff = MagicMock(id=10, name_tariff="trial7", period=7, amount=0.0, limit_ip=1)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=trial_tariff)

    from api.v1 import landing as landing_module
    upgraded = MagicMock()
    upgraded.email = "claim_ref_body@anon"
    upgraded.key = "vless://test@example.com"
    upgraded.expiry_time = int((time.time() + 7 * 24 * 3600) * 1000)
    upgraded.grace_expiry = upgraded.expiry_time + 7 * 86_400_000
    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock(return_value=upgraded)
    monkeypatch.setattr(landing_module, "build_grace_manager", lambda *a, **k: grace)
    monkeypatch.setattr(
        landing_module.TrialService, "installation_trial", AsyncMock(return_value=user)
    )

    ref_token = "ref_abcdef123456"
    link = ReferralLink(id=5, referrer_tg_id=100, token=ref_token)
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    cache = _mock_cache()
    _override_cache(cache)

    # ref_token передаётся как чистая 12-hex часть (из deeplink бота)
    resp = await api_client.post(
        f"/api/v1/landing/claim/{landing_uid}",
        json={"tg_id": 999, "ref_token": "abcdef123456"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "claimed"

    # Merge выполнен по ref_token в теле
    assert user.referral_id == 100
    mock_service_data.referral_links.get_by.assert_awaited_once()
    mock_service_data.data_service.referral_redemptions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_converted_with_ref_token_in_body_triggers_merge(
    api_client, mock_service_data
):
    """POST /mark-converted с ref_token в теле (deeplink) → merge вызван без куки."""
    from models.referrals.referral_link import ReferralLink

    landing_uid = "mc_ref_body"
    key = make_landing_key(email="mc_ref_body@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])
    mock_service_data.keys.update = AsyncMock(return_value=None)

    user = MagicMock(tg_id=999, server_id=2, trial=0, referral_id=None)
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    ref_token = "ref_abcdef123456"
    link = ReferralLink(id=5, referrer_tg_id=100, token=ref_token)
    mock_service_data.referral_links.get_by = AsyncMock(return_value=link)

    cache = _mock_cache()
    _override_cache(cache)

    resp = await api_client.post(
        f"/api/v1/landing/mark-converted/{landing_uid}",
        json={"tg_id": 999, "ref_token": ref_token},
    )
    assert resp.status_code == 200, resp.text

    # Merge выполнен по ref_token в теле
    assert user.referral_id == 100
    mock_service_data.referral_links.get_by.assert_awaited_once()
    mock_service_data.data_service.referral_redemptions.create.assert_awaited_once()


# =============================================================================
# landing_uid в LandingStateResponse / QuickKeyResponse
# =============================================================================

@pytest.mark.asyncio
async def test_state_response_has_landing_uid(api_client, mock_service_data):
    """GET /state для живого ключа → landing_uid присутствует в response."""
    from api.v1.landing import _sign_cookie

    landing_uid = "state_uid_12345"
    key = make_landing_key(email="state_uid@anon", landing_uid=landing_uid)
    mock_service_data.cache_service.keys.all = AsyncMock(return_value=[key])

    cookie = _sign_cookie(landing_uid)
    resp = await api_client.get(
        "/api/v1/landing/state", cookies={"tg_landing_id": cookie}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["landing_uid"] == landing_uid


@pytest.mark.asyncio
async def test_state_response_landing_uid_none_for_new(api_client):
    """GET /state без куки → landing_uid = null (новый посетитель)."""
    resp = await api_client.get("/api/v1/landing/state")
    assert resp.status_code == 200
    assert resp.json()["landing_uid"] is None
