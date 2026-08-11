"""
Smoke-тест для /api/v1/mobile/shared-config.

Полное покрытие (успешный путь, 500/502 ветки) — Task 3. Здесь проверяем
только, что роутер смонтирован без ошибок: запрос без X-App-Secret
возвращает 401 (auth-зависимость сработала), а не 500 (опечатка в
регистрации роутера / DI).
"""
import pytest


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
