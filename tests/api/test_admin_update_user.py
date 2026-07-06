import pytest
from unittest.mock import AsyncMock

from models import User


def make_user(tg_id=100, balance=0.0):
    return User(tg_id=tg_id, username="admin_test", balance=balance)


@pytest.mark.asyncio
async def test_update_user_sets_is_admin(api_client, mock_service_data):
    user = make_user(tg_id=123)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.users.update = AsyncMock(return_value=user)

    response = await api_client.patch(
        "/api/v1/admin/users/123",
        json={"is_admin": True},
    )

    assert response.status_code == 200
    assert user.is_admin is True