import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from models import Key, User


def make_user(tg_id=123):
    return User(tg_id=tg_id)


def make_key(email="test@vpn.ru", tg_id=123, expiry_offset_days=30, grace_offset_days=37):
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
        grace_expiry=now + grace_offset_days * 86_400_000,
    )


@pytest.mark.asyncio
async def test_channel_bonus_user_not_found(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=None)

    resp = await api_client.post("/api/v1/keys/channel-bonus?tg_id=123")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_channel_bonus_already_claimed(api_client, mock_service_data):
    user = make_user()
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)  # ON CONFLICT -> no row

    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_build.return_value = (None, None, MagicMock())
        with patch("api.v1.keys.ChannelBonusService") as mock_svc_cls:
            async def _claim(*args, **kwargs):
                from services.core.promotions.channel_bonus_service import ChannelBonusResult
                return ChannelBonusResult(status="already_claimed")

            mock_svc = MagicMock(claim=AsyncMock(side_effect=_claim))
            mock_svc_cls.return_value = mock_svc
            resp = await api_client.post("/api/v1/keys/channel-bonus?tg_id=123")

    assert resp.status_code == 200
    assert resp.json()["status"] == "already_claimed"


@pytest.mark.asyncio
async def test_channel_bonus_no_active_keys(api_client, mock_service_data):
    user = make_user()
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_build.return_value = (None, None, MagicMock())
        with patch("api.v1.keys.ChannelBonusService") as mock_svc_cls:
            from services.core.promotions.channel_bonus_service import ChannelBonusResult
            mock_svc = MagicMock(claim=AsyncMock(return_value=ChannelBonusResult(status="no_active_keys")))
            mock_svc_cls.return_value = mock_svc
            resp = await api_client.post("/api/v1/keys/channel-bonus?tg_id=123")

    assert resp.status_code == 200
    assert resp.json()["status"] == "no_active_keys"


@pytest.mark.asyncio
async def test_channel_bonus_choose_key(api_client, mock_service_data):
    user = make_user()
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_build.return_value = (None, None, MagicMock())
        with patch("api.v1.keys.ChannelBonusService") as mock_svc_cls:
            from services.core.promotions.channel_bonus_service import ChannelBonusResult
            keys = [
                {"email": "key1@vpn.ru", "expiry_time": 9999999999000, "expiry_date": "2099-01-01 00:00", "status": "active"},
                {"email": "key2@vpn.ru", "expiry_time": 9999999999000, "expiry_date": "2099-01-01 00:00", "status": "active"},
            ]
            mock_svc = MagicMock(
                claim=AsyncMock(return_value=ChannelBonusResult(status="choose_key", keys=keys))
            )
            mock_svc_cls.return_value = mock_svc
            resp = await api_client.post("/api/v1/keys/channel-bonus?tg_id=123")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "choose_key"
    assert len(data["keys"]) == 2


@pytest.mark.asyncio
async def test_channel_bonus_claimed(api_client, mock_service_data):
    user = make_user()
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    with patch("api.v1.keys.build_key_services") as mock_build:
        mock_build.return_value = (None, None, MagicMock())
        with patch("api.v1.keys.ChannelBonusService") as mock_svc_cls:
            from services.core.promotions.channel_bonus_service import ChannelBonusResult
            mock_svc = MagicMock(
                claim=AsyncMock(
                    return_value=ChannelBonusResult(
                        status="claimed",
                        email="test@vpn.ru",
                        new_expiry_time=9999999999000,
                        new_expiry_date="2099-01-01 00:00",
                    )
                )
            )
            mock_svc_cls.return_value = mock_svc
            resp = await api_client.post(
                "/api/v1/keys/channel-bonus?tg_id=123&email=test%40vpn.ru"
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "claimed"
    assert data["email"] == "test@vpn.ru"
    assert data["new_expiry_time"] == 9999999999000
