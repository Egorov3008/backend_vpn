import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot_project import _TelegramBot


@pytest.fixture
def notifier():
    return _TelegramBot("123:ABC")


@pytest.mark.asyncio
async def test_get_chat_member_returns_result(notifier):
    """Успешный ответ — возвращает result-объект со статусом."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"ok": True, "result": {"status": "member", "user": {"id": 1}}}
    with patch("bot_project.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=fake)
        mock_client_cls.return_value = client

        result = await notifier.get_chat_member("@chan", 1)
        assert result == {"status": "member", "user": {"id": 1}}


@pytest.mark.asyncio
async def test_get_chat_member_left_is_not_none(notifier):
    """Не-участник: Bot API отдаёт status='left' с ok=true — это НЕ None."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"ok": True, "result": {"status": "left"}}
    with patch("bot_project.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=fake)
        mock_client_cls.return_value = client

        result = await notifier.get_chat_member("@chan", 1)
        assert result == {"status": "left"}
        assert result is not None


@pytest.mark.asyncio
async def test_get_chat_member_not_ok_returns_none(notifier):
    """CHAT_NOT_FOUND / BOT_NOT_IN_CHANNEL: {ok:false} → None (skip)."""
    fake = MagicMock()
    fake.status_code = 200
    fake.json.return_value = {"ok": False, "description": "Bad Request: chat not found"}
    with patch("bot_project.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=fake)
        mock_client_cls.return_value = client

        assert await notifier.get_chat_member("@chan", 1) is None


@pytest.mark.asyncio
async def test_get_chat_member_non_200_returns_none(notifier):
    """HTTP non-200 → None."""
    fake = MagicMock()
    fake.status_code = 429
    fake.text = "Too Many Requests"
    with patch("bot_project.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=fake)
        mock_client_cls.return_value = client

        assert await notifier.get_chat_member("@chan", 1) is None


@pytest.mark.asyncio
async def test_get_chat_member_no_token_returns_none():
    """Пустой токен → None, без вызова API."""
    n = _TelegramBot("")
    assert await n.get_chat_member("@chan", 1) is None


@pytest.mark.asyncio
async def test_get_chat_member_network_error_returns_none(notifier):
    """Сетевой exception → None, без raise."""
    with patch("bot_project.httpx.AsyncClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = client

        assert await notifier.get_chat_member("@chan", 1) is None