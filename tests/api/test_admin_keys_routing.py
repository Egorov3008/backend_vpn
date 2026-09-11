"""GET /admin/keys (list, on `router`) and POST /admin/keys (generate, on
`destructive_router`) share the same path across two different APIRouter
instances mounted under the same /admin prefix. Pin that FastAPI resolves
both by method correctly and neither shadows the other (see code review:
this is exactly the kind of thing that silently becomes a 405 if router
include order or a prefix changes)."""
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_list_keys_get_routes_to_list_handler(api_client, mock_service_data):
    mock_service_data.keys.get_all = AsyncMock(return_value=[])
    response = await api_client.get("/api/v1/admin/keys")
    assert response.status_code == 200
    assert response.json() == {"keys": []}


@pytest.mark.asyncio
async def test_generate_keys_post_routes_to_generate_handler(api_client, mock_service_data):
    """Tariff lookup misses → 404 from inside admin_generate_key, not a
    routing-level 404/405 — proves POST reached the destructive_router
    handler rather than being shadowed by the GET list route."""
    mock_service_data.users.get_data = AsyncMock(return_value=None)
    mock_service_data.data_service.users.get = AsyncMock(return_value=None)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=None)

    response = await api_client.post(
        "/api/v1/admin/keys",
        json={"tg_id": 123, "tariff_id": 999, "server_id": 2, "number_of_months": 1},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Tariff not found"


@pytest.mark.asyncio
async def test_delete_inactive_users_not_shadowed_by_delete_user_by_id(api_client, mock_service_data):
    """DELETE /admin/users/inactive and DELETE /admin/users/{tg_id} share a method
    on the same destructive_router — this is exactly the collision fixed once
    already for the GET routes in e09e84d. Registration order (inactive route
    before the {tg_id} route in api/v1/admin.py) must keep "inactive" from
    being parsed as a tg_id path param."""
    mock_service_data.data_service.users.get_all = AsyncMock(return_value=[])
    mock_service_data.data_service.keys.get_all = AsyncMock(return_value=[])

    response = await api_client.delete("/api/v1/admin/users/inactive")

    assert response.status_code == 200
    assert response.json() == {"deleted": 0}


@pytest.mark.asyncio
async def test_delete_user_by_id_still_reachable(api_client, mock_service_data):
    """Sanity check for the same collision from the other side: a numeric
    tg_id must still route to admin_delete_user, not the inactive-users
    handler."""
    mock_service_data.users.get_data = AsyncMock(return_value=None)
    mock_service_data.data_service.users.get = AsyncMock(return_value=None)

    response = await api_client.delete("/api/v1/admin/users/123")

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"
