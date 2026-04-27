import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from models import PaymentModel


@pytest.mark.asyncio
async def test_webhook_non_payment_event_returns_ok(api_client):
    response = await api_client.post("/api/v1/payments/webhook", json={
        "type": "notification",
        "event": "payment.waiting_for_capture",
        "object": {"id": "pay_123"},
    })
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_webhook_payment_succeeded_calls_router(api_client, mock_service_data):
    with patch("api.v1.payments.build_payment_router") as mock_factory:
        mock_router = MagicMock()
        mock_router.route = AsyncMock(return_value=None)
        mock_factory.return_value = mock_router

        response = await api_client.post("/api/v1/payments/webhook", json={
            "type": "notification",
            "event": "payment.succeeded",
            "object": {"id": "pay_abc123"},
        })

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    mock_router.route.assert_called_once_with("pay_abc123")


@pytest.mark.asyncio
async def test_webhook_missing_payment_id_returns_400(api_client):
    response = await api_client.post("/api/v1/payments/webhook", json={
        "type": "notification",
        "event": "payment.succeeded",
        "object": {},
    })
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_payment_missing_tariff_returns_404(api_client, mock_service_data):
    mock_service_data.tariffs.get_data = AsyncMock(return_value=None)
    mock_service_data.users.get_data = AsyncMock(return_value=None)

    response = await api_client.post("/api/v1/payments/create", json={
        "tg_id": 123,
        "tariff_id": 999,
        "number_of_months": 1,
        "operation": "create_key",
    })
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_payment_history_empty(api_client, mock_service_data):
    """Test getting payment history when no payments exist"""
    mock_service_data.payments.get_by = AsyncMock(return_value=None)

    response = await api_client.get("/api/v1/payments/?tg_id=123")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_payment_history_multiple(api_client, mock_service_data):
    """Test getting payment history with multiple payments"""
    payments = [
        PaymentModel(
            payment_id="pay_001",
            tg_id=123,
            amount=99.99,
            status="succeeded",
            payment_type="create_key|1",
            created_at=datetime(2026, 4, 27, 10, 0, 0),
        ),
        PaymentModel(
            payment_id="pay_002",
            tg_id=123,
            amount=199.99,
            status="succeeded",
            payment_type="renew_key|user@example.com",
            created_at=datetime(2026, 4, 26, 15, 30, 0),
        ),
        PaymentModel(
            payment_id="pay_003",
            tg_id=123,
            amount=49.99,
            status="pending",
            payment_type="create_key|2",
            created_at=datetime(2026, 4, 25, 12, 0, 0),
        ),
    ]
    mock_service_data.payments.get_by = AsyncMock(return_value=payments)

    response = await api_client.get("/api/v1/payments/?tg_id=123")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["payment_id"] == "pay_001"
    assert data[0]["status"] == "succeeded"
    assert data[0]["amount"] == 99.99
    assert data[1]["payment_id"] == "pay_002"
    assert data[2]["status"] == "pending"


@pytest.mark.asyncio
async def test_get_payment_status_success(api_client, mock_service_data):
    """Test getting status of a valid payment"""
    payment = PaymentModel(
        payment_id="pay_123",
        tg_id=456,
        amount=99.99,
        status="succeeded",
        payment_type="create_key|1",
        created_at=datetime(2026, 4, 27, 10, 0, 0),
    )
    mock_service_data.payments.get_data = AsyncMock(return_value=payment)

    response = await api_client.get("/api/v1/payments/pay_123/status?tg_id=456")
    assert response.status_code == 200
    data = response.json()
    assert data["payment_id"] == "pay_123"
    assert data["status"] == "succeeded"
    assert data["tg_id"] == 456


@pytest.mark.asyncio
async def test_get_payment_status_not_found(api_client, mock_service_data):
    """Test getting status of a non-existent payment"""
    mock_service_data.payments.get_data = AsyncMock(return_value=None)

    response = await api_client.get("/api/v1/payments/pay_nonexistent/status?tg_id=456")
    assert response.status_code == 404
    assert "Payment not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_payment_status_unauthorized(api_client, mock_service_data):
    """Test getting status of a payment that belongs to a different user"""
    payment = PaymentModel(
        payment_id="pay_123",
        tg_id=789,
        amount=99.99,
        status="succeeded",
        payment_type="create_key|1",
        created_at=datetime(2026, 4, 27, 10, 0, 0),
    )
    mock_service_data.payments.get_data = AsyncMock(return_value=payment)

    response = await api_client.get("/api/v1/payments/pay_123/status?tg_id=456")
    assert response.status_code == 403
    assert "does not belong to this user" in response.json()["detail"]
