import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from models import Key, User


def make_user(tg_id=123):
    return User(tg_id=tg_id)


def make_key(email="test@vpn.ru", tg_id=123, expiry_offset_days=30):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    return Key(
        tg_id=tg_id,
        client_id="abc123",
        email=email,
        expiry_time=now + expiry_offset_days * 86_400_000,
        key="https://sub.example.com/abc",
        inbound_id=11,
        tariff_id=9,
        name_tariff="Pro",
        used_traffic=1.0,
    )


@pytest.mark.asyncio
async def test_channel_bonus_user_not_found(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=None)

    resp = await api_client.post("/api/v1/keys/channel-bonus?tg_id=123")
    assert resp.status_code == 404
