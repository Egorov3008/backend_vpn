import pytest
from unittest.mock import AsyncMock
from models import Tariff


@pytest.mark.asyncio
async def test_list_tariffs_returns_empty(api_client):
    response = await api_client.get("/api/v1/tariffs/")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_tariffs_returns_items(api_client, mock_service_data):
    mock_service_data.tariffs.get_all = AsyncMock(return_value=[
        Tariff(id=1, name_tariff="Basic", amount=99.0, period=30, traffic_limit=50, limit_ip=3)
    ])
    response = await api_client.get("/api/v1/tariffs/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == 1
    assert data[0]["name_tariff"] == "Basic"
    assert data[0]["amount"] == 99.0


@pytest.mark.asyncio
async def test_get_tariff_not_found(api_client, mock_service_data):
    mock_service_data.tariffs.get_data = AsyncMock(return_value=None)
    response = await api_client.get("/api/v1/tariffs/999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_tariff_by_id(api_client, mock_service_data):
    mock_service_data.tariffs.get_data = AsyncMock(return_value=
        Tariff(id=5, name_tariff="Pro", amount=299.0, period=30, traffic_limit=100, limit_ip=5)
    )
    response = await api_client.get("/api/v1/tariffs/5")
    assert response.status_code == 200
    assert response.json()["id"] == 5
    assert response.json()["name_tariff"] == "Pro"
