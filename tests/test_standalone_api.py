"""Tests for XUISession standalone API methods (Stage 1 / v3.2.0)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from client import (
    XUISession,
    _StandaloneClientAPI,
    XUIAuthError,
    XUIAPIError,
    _xui_circuit_breaker,
)


@pytest.fixture
def reset_circuit_breaker():
    """Сброс общего circuit breaker до/после теста (модульный singleton)."""
    _xui_circuit_breaker._state = "closed"
    _xui_circuit_breaker._consecutive_failures = 0
    _xui_circuit_breaker._consecutive_successes = 0
    _xui_circuit_breaker._opened_at = None
    yield
    _xui_circuit_breaker._state = "closed"
    _xui_circuit_breaker._consecutive_failures = 0
    _xui_circuit_breaker._consecutive_successes = 0
    _xui_circuit_breaker._opened_at = None


@pytest.fixture
def mock_service_data():
    m = MagicMock()
    m.servers = MagicMock()
    m.servers.get_data = AsyncMock(return_value=None)
    return m


@pytest.fixture
def mock_loading():
    m = MagicMock()
    m.load_server = AsyncMock()
    return m


@pytest.fixture
def xui_session(mock_service_data, mock_loading):
    return XUISession(model_service=mock_service_data, loading=mock_loading)


class TestStandaloneClientAPI:
    """Unit tests for _StandaloneClientAPI (pure httpx wrapper)."""

    @pytest.mark.asyncio
    async def test_request_raises_when_no_cookie_and_login_fails(self):
        api = _StandaloneClientAPI(
            base_url="http://localhost:2053",
            username="admin",
            password="admin",
        )
        with patch.object(
            api._client, "get", new_callable=AsyncMock
        ) as mock_get, patch.object(
            api._client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_get.return_value = MagicMock(
                status_code=200,
                cookies={},
                json=MagicMock(return_value={"obj": "csrf123"}),
                raise_for_status=MagicMock(),
            )
            mock_post.return_value = MagicMock(
                status_code=200,
                cookies={},
                raise_for_status=MagicMock(),
            )
            with pytest.raises(XUIAuthError):
                await api._ensure_auth()

    @pytest.mark.asyncio
    async def test_request_uses_existing_cookie(self):
        api = _StandaloneClientAPI(
            base_url="http://localhost:2053",
            username="admin",
            password="admin",
            session_cookie="abc123",
        )
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"success": True}),
                raise_for_status=MagicMock(),
            )
            result = await api.get("test@x.com")
            assert result == {"success": True}
            _, kwargs = mock_req.call_args
            assert kwargs["headers"]["Cookie"] == "session=abc123"

    @pytest.mark.asyncio
    async def test_add_payload_structure(self):
        api = _StandaloneClientAPI(
            base_url="http://localhost:2053",
            username="admin",
            password="admin",
            session_cookie="abc123",
        )
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={"success": True, "obj": {"id": 1}}),
                raise_for_status=MagicMock(),
            )
            await api.add(
                client_data={"email": "a@b.com", "id": "uuid"},
                inbound_ids=[1, 2],
            )
            _, kwargs = mock_req.call_args
            assert kwargs["json"] == {
                "client": {"email": "a@b.com", "id": "uuid"},
                "inboundIds": [1, 2],
            }

    @pytest.mark.asyncio
    async def test_add_raises_on_success_false(self):
        """3x-ui возвращает HTTP 200 с success:false (напр. несуществующий inbound).
        Backend НЕ должен считать это успехом — иначе появится фантомный ключ в БД."""
        api = _StandaloneClientAPI(
            base_url="http://localhost:2053",
            username="admin",
            password="admin",
            session_cookie="abc123",
        )
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value={"success": False, "msg": " (record not found)", "obj": None}
                ),
                raise_for_status=MagicMock(),
            )
            with pytest.raises(XUIAPIError):
                await api.add(
                    client_data={"email": "a@b.com", "id": "uuid"},
                    inbound_ids=[100, 99],
                )


class TestXUISessionStandaloneMethods:
    """Unit tests for XUISession facade over standalone API."""

    @pytest.mark.asyncio
    async def test_attach_to_inbounds_delegates_to_standalone(self, xui_session):
        # Arrange: fully mock py3xui layer
        xui_session._initialized = True
        xui_session.xui = MagicMock()
        xui_session.xui.client = MagicMock()
        xui_session.xui.client.cookies = MagicMock()
        xui_session.xui.client.cookies.jar = [
            MagicMock(name="session", value="sess99")
        ]
        xui_session._is_authenticated = True
        xui_session.server = MagicMock(api_url="http://panel", login="u", password="p")

        with patch.object(
            _StandaloneClientAPI, "attach", new_callable=AsyncMock
        ) as mock_attach:
            mock_attach.return_value = {"success": True}
            result = await xui_session.attach_to_inbounds(
                "user@x.com", inbound_ids=[5, 6]
            )

        assert result == {"success": True}
        mock_attach.assert_awaited_once_with("user@x.com", [5, 6])

    @pytest.mark.asyncio
    async def test_add_standalone_client_builds_correct_payload(self, xui_session):
        xui_session._initialized = True
        xui_session.xui = MagicMock()
        xui_session.xui.client = MagicMock()
        xui_session.xui.client.cookies = MagicMock()
        xui_session.xui.client.cookies.jar = []
        xui_session._is_authenticated = True
        xui_session.server = MagicMock(api_url="http://panel", login="u", password="p")

        with patch.object(
            _StandaloneClientAPI, "_ensure_auth", new_callable=AsyncMock
        ), patch.object(
            _StandaloneClientAPI, "add", new_callable=AsyncMock
        ) as mock_add:
            mock_add.return_value = {"success": True}
            result = await xui_session.add_standalone_client(
                email="user@x.com",
                client_id="550e8400-e29b-41d4-a716-446655440000",
                inbound_ids=[1],
                tg_id=123456789,
                comment="test",
            )

        assert result == {"success": True}
        call_args = mock_add.call_args[0]
        client_data, inbound_ids = call_args
        assert client_data["email"] == "user@x.com"
        assert client_data["id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert client_data["tgId"] == 123456789
        # totalGB не передаётся: все ключи безлимитные
        assert "totalGB" not in client_data
        assert client_data["subId"] == "user@x.com"
        assert inbound_ids == [1]

    @pytest.mark.asyncio
    async def test_delete_standalone_client(self, xui_session):
        xui_session._initialized = True
        xui_session.xui = MagicMock()
        xui_session.xui.client = MagicMock()
        xui_session.xui.client.cookies = MagicMock()
        xui_session.xui.client.cookies.jar = [
            MagicMock(name="session", value="sess99")
        ]
        xui_session._is_authenticated = True
        xui_session.server = MagicMock(api_url="http://panel", login="u", password="p")

        with patch.object(
            _StandaloneClientAPI, "delete", new_callable=AsyncMock
        ) as mock_delete:
            mock_delete.return_value = {"success": True}
            result = await xui_session.delete_standalone_client(
                "user@x.com", keep_traffic=True
            )

        assert result == {"success": True}
        mock_delete.assert_awaited_once_with("user@x.com", keep_traffic=True)

    @pytest.mark.asyncio
    async def test_add_client_returns_false_on_success_false(self, reset_circuit_breaker):
        """Панель ответила success:false (несуществующий inbound) → add_client
        обязан вернуть False, иначе CreateKey.proces сохранит фантомный ключ."""
        mock_server = MagicMock()
        mock_server.api_url = "http://localhost:8000"
        mock_server.login = "admin"
        mock_server.password = "admin"
        mock_model_service = MagicMock()
        mock_model_service.servers = MagicMock()
        mock_model_service.servers.get_data = AsyncMock(return_value=mock_server)

        session = XUISession(model_service=mock_model_service, loading=MagicMock())
        await session.server_init()
        session._standalone = _StandaloneClientAPI(
            base_url="http://localhost:8000", username="admin", password="admin",
            session_cookie="sess",
        )
        session._is_authenticated = True

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value={"success": False, "msg": " (record not found)", "obj": None}
                ),
                raise_for_status=MagicMock(),
            )
            result = await session.add_client(
                client_id="uuid", email="phantom@x.com", tg_id=1, limit_ip=1,
                inbound_ids=[100, 99], expiry_time=0,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_add_client_success_false_does_not_trip_circuit_breaker(
        self, reset_circuit_breaker
    ):
        """Логический провал панели (success:false) — НЕ проблема доступности:
        не должен выбивать общий circuit breaker, иначе одно неверное
        AVAILABLE_CONNECTIONS ломает renewal/delete для существующих юзеров."""
        mock_server = MagicMock()
        mock_server.api_url = "http://localhost:8000"
        mock_server.login = "admin"
        mock_server.password = "admin"
        mock_model_service = MagicMock()
        mock_model_service.servers = MagicMock()
        mock_model_service.servers.get_data = AsyncMock(return_value=mock_server)

        session = XUISession(model_service=mock_model_service, loading=MagicMock())
        await session.server_init()
        session._standalone = _StandaloneClientAPI(
            base_url="http://localhost:8000", username="admin", password="admin",
            session_cookie="sess",
        )
        session._is_authenticated = True

        # 5 последовательных success:false — достаточно чтобы открыть breaker,
        # если бы они считались как failure (fail_max=5).
        with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = MagicMock(
                status_code=200,
                json=MagicMock(
                    return_value={"success": False, "msg": " (record not found)", "obj": None}
                ),
                raise_for_status=MagicMock(),
            )
            for _ in range(6):
                await session.add_client(
                    client_id="uuid", email="phantom@x.com", tg_id=1, limit_ip=1,
                    inbound_ids=[100, 99], expiry_time=0,
                )

        assert _xui_circuit_breaker._state == "closed"
        assert _xui_circuit_breaker._consecutive_failures == 0
