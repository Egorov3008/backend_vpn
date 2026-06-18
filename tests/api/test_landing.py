"""
Smoke-тесты для /api/v1/landing/*.

Полное покрытие (с реальным 3x-UI и БД) — после деплоя на dev-стенд.
Здесь проверяем, что эндпоинты существуют, отвечают и не падают на 500.
"""
import time

import pytest
from unittest.mock import AsyncMock, MagicMock


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
    mock_service_data.cache_service.keys.all = MagicMock(return_value=[key])
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
    mock_service_data.cache_service.keys.all = MagicMock(return_value=[expired_key])

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
