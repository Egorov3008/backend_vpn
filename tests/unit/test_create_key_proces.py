"""Tests for CreateKey.proces — связь с панелью и сохранение в БД.

Регрессия бага dp5649: при провале add_client (панель ответила success:false)
proces сохранял фантомный ключ в БД и рапортовал успех, хотя в панели клиента нет.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from config import settings
from services.core.keys.utils.create_key import CreateKey
from services.system.maintenance import PanelMaintenanceError, maintenance_mode


@pytest.fixture(autouse=True)
def _maintenance_mode_off(monkeypatch):
    """conn в этих тестах — MagicMock, не asyncpg.Pool; отключаем реальную БД-проверку."""
    monkeypatch.setattr(maintenance_mode, "is_enabled", AsyncMock(return_value=False))


def _make_key():
    k = MagicMock()
    k.client_id = "uuid-1"
    k.email = "phantom@x.com"
    k.tg_id = 1
    k.limit_ip = 1
    k.inbound_ids = [100, 99]
    k.inbound_id = 100
    k.expiry_time = 0
    k.key = "https://sub.example/phantom@x.com"
    return k


def _make_create_key(add_client_return):
    model_data = MagicMock()
    model_data.keys = MagicMock()
    model_data.keys.save_data = AsyncMock()
    xui_session = MagicMock()
    xui_session.add_client = AsyncMock(return_value=add_client_return)
    expiry = MagicMock()
    formation = MagicMock()
    formation.form_new_key = AsyncMock(return_value=_make_key())
    return CreateKey(
        model_data=model_data,
        xui_session=xui_session,
        expiry=expiry,
        formation=formation,
    ), model_data, xui_session


@pytest.mark.asyncio
async def test_proces_returns_none_and_does_not_save_when_add_client_fails():
    """Панель не создала клиента (add_client -> False) → proces НЕ сохраняет
    фантомный ключ в БД и возвращает None (endpoint ответит 500)."""
    create_key, model_data, xui_session = _make_create_key(add_client_return=False)
    tariff = MagicMock(id=1, amount=100, limit_ip=1, period=1)

    result = await create_key.proces(
        tg_id=1, tariff=tariff, server_id=2, conn=MagicMock()
    )

    assert result is None
    model_data.keys.save_data.assert_not_called()
    xui_session.add_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_proces_saves_and_returns_link_when_add_client_succeeds():
    """Контракт успеха: add_client -> True → ключ сохраняется, возвращается ссылка."""
    create_key, model_data, xui_session = _make_create_key(add_client_return=True)
    tariff = MagicMock(id=1, amount=100, limit_ip=1, period=1)

    result = await create_key.proces(
        tg_id=1, tariff=tariff, server_id=2, conn=MagicMock()
    )

    assert result is not None
    assert result["email"] == "phantom@x.com"
    model_data.keys.save_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_proces_passes_external_sub_url_as_subscription_link_to_add_client(monkeypatch):
    """add_client должен получать subscription_link, равный EXTERNAL_SUB_URL
    как есть (панельная externalLinks-ссылка), а НЕ key.key (это отдельная,
    рабочая ссылка подписки на XUI_SUB, отдаётся юзеру отдельно)."""
    # Тест не должен зависеть от того, задан ли EXTERNAL_SUB_URL в реальном
    # .env окружения, где выполняются тесты — фиксируем непустое значение.
    monkeypatch.setattr(settings, "external_subscription_url", "https://sub.example.com/external-test")
    create_key, model_data, xui_session = _make_create_key(add_client_return=True)
    tariff = MagicMock(id=1, amount=100, limit_ip=1, period=1)

    await create_key.proces(tg_id=1, tariff=tariff, server_id=2, conn=MagicMock())

    _, kwargs = xui_session.add_client.await_args
    assert kwargs["subscription_link"] == settings.external_subscription_url


@pytest.mark.asyncio
async def test_proces_blocked_when_maintenance_mode_enabled(monkeypatch):
    """Режим профилактики включён → PanelMaintenanceError до обращения к панели."""
    monkeypatch.setattr(maintenance_mode, "is_enabled", AsyncMock(return_value=True))
    create_key, model_data, xui_session = _make_create_key(add_client_return=True)
    tariff = MagicMock(id=1, amount=100, limit_ip=1, period=1)

    with pytest.raises(PanelMaintenanceError):
        await create_key.proces(tg_id=1, tariff=tariff, server_id=2, conn=MagicMock())

    xui_session.add_client.assert_not_awaited()
    model_data.keys.save_data.assert_not_called()