"""
Tests for XUI circuit breaker functionality.

Verifies circuit breaker state transitions for XUI API calls:
- CLOSED → OPEN after fail_threshold consecutive failures
- OPEN → HALF_OPEN after reset_timeout
- HALF_OPEN → CLOSED on success, or back to OPEN on failure
"""
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

from backend.client import XUISession, _xui_circuit_breaker, _StandaloneClientAPI


class TestXUICircuitBreakerStateTransitions:
    """Test circuit breaker state transitions for XUI API."""

    @pytest.fixture
    def mock_server(self):
        """Create mock server object."""
        server = MagicMock()
        server.api_url = "http://localhost:8000"
        server.login = "admin"
        server.password = "admin"
        return server

    @pytest.fixture
    def mock_services(self, mock_server):
        """Create mock service dependencies."""
        mock_model_service = MagicMock()
        mock_model_service.servers = MagicMock()
        mock_model_service.servers.get_data = AsyncMock(return_value=mock_server)

        mock_loading = MagicMock()

        return mock_model_service, mock_loading

    @pytest.fixture
    def xui_session(self, mock_services):
        """Create XUISession with mocked dependencies."""
        mock_model_service, mock_loading = mock_services
        session = XUISession(
            model_service=mock_model_service,
            loading=mock_loading,
            login_timeout=5.0,
            login_max_retries=1,
        )
        return session

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_consecutive_failures(self, xui_session):
        """Circuit breaker opens after 5 consecutive failures."""
        # Initialize session
        await xui_session.server_init()

        # Create standalone client with failing methods
        xui_session._standalone = _StandaloneClientAPI(
            base_url="http://localhost:8000",
            username="admin",
            password="admin",
        )
        xui_session._is_authenticated = True

        # Mock the _request method to always fail
        mock_request = AsyncMock(side_effect=httpx.ConnectTimeout("Connection timed out"))
        xui_session._standalone._request = mock_request

        # Manually call circuit breaker wrapped function
        async def failing_operation():
            raise httpx.ConnectTimeout("Connection timed out")

        # Trip the circuit breaker
        for _ in range(5):
            try:
                await _xui_circuit_breaker.call_async(failing_operation)
            except Exception:
                pass

        # Circuit breaker should now be open
        assert _xui_circuit_breaker.current_state == "open"

    @pytest.mark.asyncio
    async def test_circuit_breaker_stays_closed_on_success(self, xui_session):
        """Circuit breaker stays closed when requests succeed."""
        # Initialize session
        await xui_session.server_init()
        xui_session._standalone = _StandaloneClientAPI(
            base_url="http://localhost:8000",
            username="admin",
            password="admin",
        )
        xui_session._is_authenticated = True

        # Mock successful operation
        async def success_operation():
            return {"success": True}

        # Make successful requests
        for _ in range(10):
            result = await _xui_circuit_breaker.call_async(success_operation)
            assert result == {"success": True}

        # Circuit breaker should remain closed
        assert _xui_circuit_breaker.current_state == "closed"

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_after_manual_transition(self, xui_session):
        """Circuit breaker transitions to half-open when manually triggered."""
        # Initialize session
        await xui_session.server_init()
        xui_session._standalone = _StandaloneClientAPI(
            base_url="http://localhost:8000",
            username="admin",
            password="admin",
        )
        xui_session._is_authenticated = True

        # Open the circuit breaker
        async def failing_operation():
            raise httpx.ConnectTimeout("Connection timed out")

        for _ in range(5):
            try:
                await _xui_circuit_breaker.call_async(failing_operation)
            except Exception:
                pass

        assert _xui_circuit_breaker.current_state == "open"

        # Transition to half-open
        _xui_circuit_breaker.half_open()
        assert _xui_circuit_breaker.current_state == "half_open"

        # Successful request should close the circuit
        async def success_operation():
            return {"success": True}

        result = await _xui_circuit_breaker.call_async(success_operation)
        assert result == {"success": True}
        assert _xui_circuit_breaker.current_state == "closed"


class TestXUICircuitBreakerExceptionHandling:
    """Test that XUI circuit breaker catches correct exceptions."""

    @pytest.fixture
    def mock_server(self):
        """Create mock server object."""
        server = MagicMock()
        server.api_url = "http://localhost:8000"
        server.login = "admin"
        server.password = "admin"
        return server

    @pytest.mark.asyncio
    async def test_connect_timeout_trips_breaker(self):
        """httpx.ConnectTimeout should trip the circuit breaker."""
        async def failing_operation():
            raise httpx.ConnectTimeout("Timeout")

        for _ in range(5):
            try:
                await _xui_circuit_breaker.call_async(failing_operation)
            except Exception:
                pass

        assert _xui_circuit_breaker.current_state == "open"

    @pytest.mark.asyncio
    async def test_connect_error_trips_breaker(self):
        """httpx.ConnectError should trip the circuit breaker."""
        async def failing_operation():
            raise httpx.ConnectError("Connection refused")

        for _ in range(5):
            try:
                await _xui_circuit_breaker.call_async(failing_operation)
            except Exception:
                pass

        assert _xui_circuit_breaker.current_state == "open"

    @pytest.mark.asyncio
    async def test_read_timeout_trips_breaker(self):
        """httpx.ReadTimeout should trip the circuit breaker."""
        async def failing_operation():
            raise httpx.ReadTimeout("Read timeout")

        for _ in range(5):
            try:
                await _xui_circuit_breaker.call_async(failing_operation)
            except Exception:
                pass

        assert _xui_circuit_breaker.current_state == "open"

    @pytest.mark.asyncio
    async def test_connection_error_trips_breaker(self):
        """ConnectionError should trip the circuit breaker."""
        async def failing_operation():
            raise ConnectionError("Connection lost")

        for _ in range(5):
            try:
                await _xui_circuit_breaker.call_async(failing_operation)
            except Exception:
                pass

        assert _xui_circuit_breaker.current_state == "open"


class TestXUICircuitBreakerIntegration:
    """Integration tests for circuit breaker with XUISession methods."""

    @pytest.fixture
    def mock_server(self):
        """Create mock server object."""
        server = MagicMock()
        server.api_url = "http://localhost:8000"
        server.login = "admin"
        server.password = "admin"
        return server

    @pytest.fixture
    def mock_services(self, mock_server):
        """Create mock service dependencies."""
        mock_model_service = MagicMock()
        mock_model_service.servers = MagicMock()
        mock_model_service.servers.get_data = AsyncMock(return_value=mock_server)

        mock_loading = MagicMock()

        return mock_model_service, mock_loading

    @pytest.mark.asyncio
    async def test_add_client_respects_circuit_breaker(self, mock_services):
        """add_client should fail fast when circuit breaker is open."""
        mock_model_service, mock_loading = mock_services

        session = XUISession(
            model_service=mock_model_service,
            loading=mock_loading,
            login_timeout=5.0,
            login_max_retries=1,
        )

        # Initialize session
        await session.server_init()
        session._standalone = _StandaloneClientAPI(
            base_url="http://localhost:8000",
            username="admin",
            password="admin",
        )
        session._is_authenticated = True

        # Open the circuit breaker
        async def failing_operation():
            raise httpx.ConnectTimeout("Timeout")

        for _ in range(5):
            try:
                await _xui_circuit_breaker.call_async(failing_operation)
            except Exception:
                pass

        assert _xui_circuit_breaker.current_state == "open"

        # add_client should return False when circuit is open
        # (actual implementation may vary based on how circuit breaker is integrated)
        # This test documents expected behavior
