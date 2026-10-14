"""Публичные платежи: тарифы вне AVAILABLE_RATES недоступны, а суммы и скидки задаёт только бэкенд."""

from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_pool
from app.main import app
from models import Tariff
from services.api_clients.service import ApiClientService
from tests.services.test_api_clients_service import FakeApiClientsPool


@pytest.fixture
def fake_pool():
    return FakeApiClientsPool()


@pytest.fixture(autouse=True)
def public_available_rates(monkeypatch):
    monkeypatch.setattr("api.v1.public.payments.AVAILABLE_RATES_LIST", [1, 5])


async def _payments_key(fake_pool):
    _, raw_key = await ApiClientService(fake_pool).create(
        "Partner A", scopes=["payments:write", "payments:read"]
    )
    return {"Authorization": f"Bearer {raw_key}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/public/payments/create", "/api/v1/public/payments/quotes"])
async def test_hidden_tariff_is_404_before_tariff_lookup(
    api_client, fake_pool, mock_service_data, path
):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.tariffs.get_data = AsyncMock(
        return_value=Tariff(id=999, name_tariff="Hidden", amount=100.0)
    )
    headers = await _payments_key(fake_pool)

    response = await api_client.post(
        path, json={"tg_id": 1, "tariff_id": 999}, headers=headers
    )

    assert response.status_code == 404
    mock_service_data.tariffs.get_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_create_drops_client_amount_and_referral_discount(
    api_client, fake_pool, mock_service_data, monkeypatch
):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.tariffs.get_data = AsyncMock(
        return_value=Tariff(id=1, name_tariff="Basic", amount=100.0)
    )
    create = AsyncMock(
        return_value={"payment_id": "p1", "confirmation_url": "https://pay", "amount": 100.0}
    )
    monkeypatch.setattr("api.v1.public.payments.create_payment", create)
    headers = await _payments_key(fake_pool)

    response = await api_client.post(
        "/api/v1/public/payments/create",
        json={"tg_id": 1, "tariff_id": 1, "amount": 1.0, "referral_discount": 99.0},
        headers=headers,
    )

    assert response.status_code == 200
    sent = create.await_args.kwargs["body"]
    assert sent.amount is None
    assert sent.referral_discount is None
    assert sent.tariff_id == 1


@pytest.mark.asyncio
async def test_public_create_rejects_free_tariff(api_client, fake_pool, mock_service_data):
    app.dependency_overrides[get_pool] = lambda: fake_pool
    mock_service_data.tariffs.get_data = AsyncMock(
        return_value=Tariff(id=1, name_tariff="Free", amount=0.0)
    )
    headers = await _payments_key(fake_pool)

    response = await api_client.post(
        "/api/v1/public/payments/create",
        json={"tg_id": 1, "tariff_id": 1},
        headers=headers,
    )

    assert response.status_code == 400
