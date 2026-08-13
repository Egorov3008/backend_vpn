"""
Тесты для /api/v1/mobile/shared-config.

Смоук (роутер смонтирован, 401 без заголовка) + полное покрытие веток
(неверный секрет, 500 при непровизионированном/ненайденном shared-ключе,
200 при успешном скачивании subscription, 502 при сбое скачивания) — Task 3.

Мокирование скачивания subscription-URL — тем же приёмом, что и в
test_landing.py (подмена urllib.request.urlopen на fake с FakeResponse).
Мокирование service_data/pool — тем же приёмом, что и в смоук-тесте ниже
(api_client override из tests/api/conftest.py + monkeypatch mock_service_data).
"""
import base64
import time

import pytest
from unittest.mock import AsyncMock

from models import Key


@pytest.fixture(autouse=True)
def _clear_vless_cache():
    """Изолирует тесты друг от друга: get_shared_config теперь кеширует
    успешный результат _download_and_extract_vless в module-level
    _VLESS_CACHE, keyed by subscription URL (Fix 1). Несколько тестов ниже
    переиспользуют один и тот же URL (make_shared_key's default), поэтому
    без очистки кеша между тестами успешный результат одного теста мог бы
    "просочиться" в другой (например, замаскировать ожидаемый 502)."""
    from api.v1 import mobile_mvp

    mobile_mvp._VLESS_CACHE.clear()
    yield
    mobile_mvp._VLESS_CACHE.clear()


@pytest.mark.asyncio
async def test_shared_config_no_secret_header_returns_401(api_client):
    """Без заголовка X-App-Secret → 401, не 500.

    api_client (см. tests/api/conftest.py) уже переопределяет get_service_data
    и get_pool, но НЕ verify_app_secret — этот эндпоинт должен требовать его
    независимо от verify_bot_secret override, которым пользуются остальные
    роутеры.
    """
    response = await api_client.get("/api/v1/mobile/shared-config")
    assert response.status_code == 401


def make_shared_key(email="shared@vpn", key_url="https://example.com/sub"):
    """Shared VPN-ключ (без per-user/landing-полей — они здесь не нужны)."""
    return Key(
        tg_id=0,
        client_id="uuid-shared",
        email=email,
        expiry_time=int((time.time() + 30 * 24 * 3600) * 1000),
        key=key_url,
        inbound_id=13,
        limit_ip=0,
    )


class _FakeResponse:
    """Мок urllib.request.urlopen(...) — контекстный менеджер с .read()."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _patch_no_sleep(monkeypatch):
    """Отключить реальный time.sleep между retry-попытками скачивания
    (mobile_mvp._download_and_extract_vless делает до 3 попыток с
    time.sleep(0.5*attempt) — без этого патча провальный тест 502 занял бы
    ~1.5с)."""
    from api.v1 import mobile_mvp

    monkeypatch.setattr(mobile_mvp.time, "sleep", lambda *_a, **_k: None)


@pytest.mark.asyncio
async def test_shared_config_wrong_secret_returns_401(api_client, monkeypatch):
    """Заголовок X-App-Secret присутствует, но не совпадает → 401.

    Отдельно от смоук-теста (там заголовок вовсе отсутствует) — здесь
    проверяем ветку сравнения значения, а не только "заголовок пуст".
    """
    from config import settings

    monkeypatch.setattr(settings, "mvp_app_secret", "correct-secret")

    response = await api_client.get(
        "/api/v1/mobile/shared-config",
        headers={"X-App-Secret": "wrong-secret"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_shared_config_key_not_configured_returns_500(
    api_client, mock_service_data, monkeypatch
):
    """Верный секрет, но settings.mvp_shared_key_email пуст → 500
    (ошибка провижининга/деплоя, не запроса)."""
    from config import settings

    monkeypatch.setattr(settings, "mvp_app_secret", "correct-secret")
    monkeypatch.setattr(settings, "mvp_shared_key_email", "")

    response = await api_client.get(
        "/api/v1/mobile/shared-config",
        headers={"X-App-Secret": "correct-secret"},
    )
    assert response.status_code == 500
    # Ни кеш, ни БД даже не должны опрашиваться — email пуст, короткое замыкание.
    mock_service_data.keys.get_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_config_key_not_found_returns_500(
    api_client, mock_service_data, monkeypatch
):
    """Верный секрет, email настроен, но ключ не найден ни в кеше, ни в БД → 500."""
    from config import settings

    monkeypatch.setattr(settings, "mvp_app_secret", "correct-secret")
    monkeypatch.setattr(settings, "mvp_shared_key_email", "shared@vpn")

    # mock_service_data по умолчанию (см. conftest.py): keys.get_data → None,
    # data_service.keys.get → None — обе ветки поиска "не найдено".
    response = await api_client.get(
        "/api/v1/mobile/shared-config",
        headers={"X-App-Secret": "correct-secret"},
    )
    assert response.status_code == 500
    mock_service_data.keys.get_data.assert_awaited_once_with("shared@vpn")
    mock_service_data.data_service.keys.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_config_success_returns_vless_and_expiry(
    api_client, mock_service_data, monkeypatch
):
    """Верный секрет, ключ найден в кеше, скачивание subscription успешно
    и содержит vless:// строку → 200 с vless_uri и expiry_time из ключа."""
    from config import settings

    monkeypatch.setattr(settings, "mvp_app_secret", "correct-secret")
    monkeypatch.setattr(settings, "mvp_shared_key_email", "shared@vpn")

    key = make_shared_key(email="shared@vpn")
    mock_service_data.keys.get_data = AsyncMock(return_value=key)

    vless_url = "vless://uuid@example.com:443?encryption=none&security=tls"
    body = f"{vless_url}\n".encode()

    import urllib.request

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=None: _FakeResponse(body)
    )

    response = await api_client.get(
        "/api/v1/mobile/shared-config",
        headers={"X-App-Secret": "correct-secret"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["vless_uri"] == vless_url
    assert data["expiry_time"] == key.expiry_time
    # Ключ найден в кеше — фоллбэк в БД не должен вызываться.
    mock_service_data.data_service.keys.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_config_success_base64_with_trailing_newline(
    api_client, mock_service_data, monkeypatch
):
    """Fix 4: base64-encoded subscription-тело с trailing '\\n' должно
    успешно распарситься, а не падать со 502.

    base64.b64decode(..., validate=True) отвергает ЛЮБОЙ символ вне
    base64-алфавита, включая whitespace по краям — реальные subscription-
    серверы часто дописывают завершающий перевод строки к base64-телу.
    Регрессионный тест на ветку, которая раньше не покрывалась вовсе.
    """
    from config import settings

    monkeypatch.setattr(settings, "mvp_app_secret", "correct-secret")
    monkeypatch.setattr(settings, "mvp_shared_key_email", "shared@vpn")

    key = make_shared_key(email="shared@vpn", key_url="https://example.com/sub-b64")
    mock_service_data.keys.get_data = AsyncMock(return_value=key)

    vless_url = "vless://uuid@example.com:443?encryption=none&security=tls"
    # base64-энкодим само subscription-тело (с внутренним '\n' перед энкодингом,
    # как это часто делают Happ/Sing-box), а ЗАТЕМ дописываем trailing '\n'
    # СНАРУЖИ base64-блока — это и есть баг: b64decode(..., validate=True)
    # падает именно на этом внешнем '\n', если его не strip()-нуть сначала.
    encoded = base64.b64encode(f"{vless_url}\n".encode())
    body = encoded + b"\n"

    import urllib.request

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=None: _FakeResponse(body)
    )

    response = await api_client.get(
        "/api/v1/mobile/shared-config",
        headers={"X-App-Secret": "correct-secret"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["vless_uri"] == vless_url


@pytest.mark.asyncio
async def test_shared_config_download_failure_returns_502(
    api_client, mock_service_data, monkeypatch
):
    """Верный секрет, ключ найден, но скачивание subscription не удалось
    (сетевая ошибка на всех попытках) → 502."""
    from config import settings

    monkeypatch.setattr(settings, "mvp_app_secret", "correct-secret")
    monkeypatch.setattr(settings, "mvp_shared_key_email", "shared@vpn")

    key = make_shared_key(email="shared@vpn")
    mock_service_data.keys.get_data = AsyncMock(return_value=key)

    _patch_no_sleep(monkeypatch)

    import urllib.request

    def _raise(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)

    response = await api_client.get(
        "/api/v1/mobile/shared-config",
        headers={"X-App-Secret": "correct-secret"},
    )
    assert response.status_code == 502
