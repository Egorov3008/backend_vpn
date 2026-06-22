"""
Regression-тест: extend_client_key НЕ должен вызывать reset_traffic.

Воспроизводит баг с ключом 6cx7ah: после продления expiry_time становился 0
(1 января 1970), потому что 3x-ui resetTraffic обнуляет totalGB и expiryTime.

Баг: extend_client_key вызывал update(..., expiryTime=..., totalGB=...),
а затем reset_traffic, который обнулял expiryTime и totalGB на панели.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_extend_client_key_does_not_call_reset_traffic():
    """extend_client_key должен обновлять клиента, но НЕ вызывать reset_traffic."""
    from client import XUISession, PanelClient
    from models import Key
    from services.core.data.service import ServiceDataModel

    # Создаём моки
    model_service = MagicMock(spec=ServiceDataModel)
    model_service.servers = MagicMock()
    model_service.servers.get_data = AsyncMock(return_value=None)

    loading = MagicMock()
    loading.load = AsyncMock(return_value=None)

    session = XUISession(model_service, loading)

    # Мокаем _ensure_standalone и auth
    session._ensure_standalone = AsyncMock()
    session.ensure_auth = AsyncMock()
    session._is_authenticated = True

    # Создаём моки для standalone API
    standalone = MagicMock()
    standalone.get = AsyncMock(return_value={
        "obj": {
            "client": {
                "id": 191,
                "email": "test@example.com",
                "tgId": 123456,
                "limitIp": 3,
                "totalGB": 0,
                "expiryTime": 1000000000000,
                "enable": True,
                "flow": "xtls-rprx-vision",
                "subId": "test@example.com",
                "group": "",
                "comment": "",
                "inboundIds": [39, 82],
            },
            "inboundIds": [39, 82],
        }
    })
    standalone.update = AsyncMock(return_value={"success": True})
    standalone.reset_traffic = AsyncMock()

    session._standalone = standalone

    # Ключ с безлимитным тарифом и будущей датой истечения
    key = Key(
        tg_id=123456,
        client_id="test-uuid",
        email="test@example.com",
        expiry_time=1783337068246,  # 5 июля 2026
        key="https://sub.example.com/test@example.com",
        inbound_id=1,
        limit_ip=3,
    )

    # Вызываем продление
    result = await session.extend_client_key(key)

    # Проверяем, что update был вызван с полным payload'ом
    standalone.update.assert_awaited_once()
    update_call_args = standalone.update.await_args
    assert update_call_args.args[0] == "test@example.com"
    update_payload = update_call_args.args[1]
    assert update_payload["expiryTime"] == 1783337068246
    # Поля из панели должны сохраниться, иначе 3x-ui их обнулит
    assert update_payload["id"] == "191"
    assert update_payload["tgId"] == 123456
    assert update_payload["enable"] is True
    assert update_payload["limitIp"] == 3
    assert update_payload["subId"] == "test@example.com"
    assert update_payload["inboundIds"] == [39, 82]
    # totalGB передаётся ровно таким, как на панели (0 для безлимитных)
    assert update_payload.get("totalGB") == 0

    # ГЛАВНОЕ: reset_traffic НЕ должен быть вызван!
    standalone.reset_traffic.assert_not_called()

    assert result is True


@pytest.mark.asyncio
async def test_extend_client_key_does_not_pass_total_gb():
    """extend_client_key НЕ передаёт totalGB на панель (безлимитные ключи)."""
    from client import XUISession
    from models import Key
    from services.core.data.service import ServiceDataModel

    model_service = MagicMock(spec=ServiceDataModel)
    model_service.servers = MagicMock()
    model_service.servers.get_data = AsyncMock(return_value=None)

    loading = MagicMock()
    loading.load = AsyncMock(return_value=None)

    session = XUISession(model_service, loading)
    session._ensure_standalone = AsyncMock()
    session.ensure_auth = AsyncMock()
    session._is_authenticated = True

    standalone = MagicMock()
    standalone.get = AsyncMock(return_value={
        "obj": {
            "client": {
                "id": 192,
                "email": "paid@example.com",
                "tgId": 123456,
                "limitIp": 3,
                "totalGB": 0,
                "expiryTime": 1000000000000,
                "enable": True,
                "flow": "xtls-rprx-vision",
                "subId": "paid@example.com",
                "inboundIds": [39, 82],
            },
            "inboundIds": [39, 82],
        }
    })
    standalone.update = AsyncMock(return_value={"success": True})
    standalone.reset_traffic = AsyncMock()

    session._standalone = standalone

    key = Key(
        tg_id=123456,
        client_id="paid-uuid",
        email="paid@example.com",
        expiry_time=1783337068246,
        key="https://sub.example.com/paid@example.com",
        inbound_id=1,
        limit_ip=3,
    )

    result = await session.extend_client_key(key)

    # update вызван с полным payload'ом
    standalone.update.assert_awaited_once()
    update_payload = standalone.update.await_args.args[1]
    # поля панели сохранены
    assert update_payload["id"] == "192"
    assert update_payload["tgId"] == 123456
    assert update_payload["enable"] is True
    assert update_payload["expiryTime"] == 1783337068246
    assert update_payload["limitIp"] == 3
    assert update_payload["inboundIds"] == [39, 82]
    # totalGB передаётся ровно таким, как на панели
    assert update_payload.get("totalGB") == 0

    # reset_traffic НЕ вызван
    standalone.reset_traffic.assert_not_called()

    assert result is True


@pytest.mark.asyncio
async def test_update_standalone_client_builds_full_payload_from_panel():
    """update_standalone_client должен самостоятельно построить полный payload,
    подгрузив текущее состояние клиента с панели."""
    from client import XUISession
    from services.core.data.service import ServiceDataModel

    model_service = MagicMock(spec=ServiceDataModel)
    model_service.servers = MagicMock()
    model_service.servers.get_data = AsyncMock(return_value=None)

    loading = MagicMock()
    loading.load = AsyncMock(return_value=None)

    session = XUISession(model_service, loading)
    session._ensure_standalone = AsyncMock()
    session.ensure_auth = AsyncMock()
    session._is_authenticated = True

    standalone = MagicMock()
    standalone.get = AsyncMock(return_value={
        "obj": {
            "client": {
                "id": 200,
                "email": "full@example.com",
                "tgId": 111,
                "limitIp": 2,
                "totalGB": 0,
                "expiryTime": 1700000000000,
                "enable": True,
                "flow": "xtls-rprx-vision",
                "subId": "full@example.com",
                "inboundIds": [39],
            }
        }
    })
    standalone.update = AsyncMock(return_value={"success": True})

    session._standalone = standalone

    result = await session.update_standalone_client(
        "full@example.com",
        expiryTime=1783337068246,
        enable=True,
    )

    standalone.get.assert_awaited_once_with("full@example.com")
    standalone.update.assert_awaited_once()
    payload = standalone.update.await_args.args[1]
    assert payload["expiryTime"] == 1783337068246
    assert payload["enable"] is True
    # поля, не переданные в kwargs, должны быть взяты из панели, а не обнулены
    assert payload["id"] == "200"
    assert payload["tgId"] == 111
    assert payload["limitIp"] == 2
    assert payload["subId"] == "full@example.com"
    assert payload["inboundIds"] == [39]

    assert result == {"success": True}
