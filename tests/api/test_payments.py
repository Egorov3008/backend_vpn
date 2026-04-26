import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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
