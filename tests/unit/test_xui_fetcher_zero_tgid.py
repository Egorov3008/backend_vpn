"""
Regression-тест: клиенты с tgId=0 на панели НЕ должны отбрасываться
при извлечении, иначе синхронизатор ошибочно посчитает их
"удалёнными с панели" и удалит из БД.

Воспроизводит баг с пользователем 397349989 (email=6cx7ah на панели),
у которого на панели tgId=0 (потерян/обнулён), но ключ живой.
"""
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_extract_clients_includes_zero_tgid():  # noqa: PT006
    """Клиент с tgId=0 (но валидным email) должен попадать в valid_clients."""
    from services.synchron.xui_fetcher import XUIFetcher

    fetcher = XUIFetcher()

    # Эмулируем raw ответ от 3x-ui standalone API: клиент 6cx7ah с tgId=0
    raw = [
        {
            "id": "26af8d99-dbcd-4564-ab14-a5805298262c",
            "email": "6cx7ah",
            "tgId": 0,            # <-- потерян/обнулён на панели
            "totalGB": 0,
            "expiryTime": 1783337068246,
            "enable": True,
            "inboundIds": [39, 51, 64],
        }
    ]

    xui_session = AsyncMock()
    xui_session.list_clients = AsyncMock(return_value=raw)

    result = await fetcher.extract_clients(xui_session)

    assert len(result) == 1, (
        "Клиент с tgId=0, но валидным email не должен отбрасываться — "
        "иначе синхронизатор удалит его из БД как orphaned"
    )
    assert result[0].email == "6cx7ah"
    assert result[0].tg_id == 0


@pytest.mark.asyncio
async def test_extract_clients_still_filters_invalid_email():  # noqa: PT006
    """Клиенты с пустым email по-прежнему отбрасываются."""
    from services.synchron.xui_fetcher import XUIFetcher

    fetcher = XUIFetcher()

    raw = [
        {"id": "uuid-1", "email": "", "tgId": 123, "inboundIds": [1]},
        {"id": "uuid-2", "email": "valid", "tgId": 0, "inboundIds": [1]},
    ]
    xui_session = AsyncMock()
    xui_session.list_clients = AsyncMock(return_value=raw)

    result = await fetcher.extract_clients(xui_session)

    emails = [c.email for c in result]
    assert "valid" in emails
    assert "" not in emails
