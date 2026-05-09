import pytest
from unittest.mock import AsyncMock, MagicMock
from models import Key


def make_key(email="test@vpn.ru", tg_id=123):
    return Key(
        tg_id=tg_id,
        client_id="abc123",
        email=email,
        expiry_time=9999999999000,
        key="https://sub.example.com/abc",
        inbound_id=11,
        tariff_id=9,
        name_tariff="Pro",
        total_gb=50 * (1024 ** 3),
        used_traffic=1.0,
    )


@pytest.mark.asyncio
async def test_list_keys_empty(api_client, mock_service_data):
    mock_service_data.data_service.keys.filter = AsyncMock(return_value=[])
    response = await api_client.get("/api/v1/keys/?tg_id=123")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_keys_single(api_client, mock_service_data):
    mock_service_data.data_service.keys.filter = AsyncMock(return_value=[make_key()])
    response = await api_client.get("/api/v1/keys/?tg_id=123")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["email"] == "test@vpn.ru"


@pytest.mark.asyncio
async def test_list_keys_multiple(api_client, mock_service_data):
    mock_service_data.data_service.keys.filter = AsyncMock(return_value=[
        make_key("key1@vpn.ru"),
        make_key("key2@vpn.ru"),
    ])
    response = await api_client.get("/api/v1/keys/?tg_id=123")
    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_key_not_found(api_client, mock_service_data):
    mock_service_data.keys.get_data = AsyncMock(return_value=None)
    response = await api_client.get("/api/v1/keys/notexist@vpn.ru")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_key_detail(api_client, mock_service_data):
    key = make_key()
    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    response = await api_client.get("/api/v1/keys/test@vpn.ru")
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@vpn.ru"
    assert "status_text" in data
    assert "days_left" in data
    assert "is_active" in data


from unittest.mock import patch, AsyncMock as _AsyncMock


def make_user(tg_id=123, trial=0):
    u = MagicMock()
    u.tg_id = tg_id
    u.trial = trial
    u.server_id = 2
    return u


def make_tariff(tariff_id=10, amount=0.0):
    t = MagicMock()
    t.id = tariff_id
    t.amount = amount
    t.name_tariff = "Пробный"
    return t


@pytest.mark.asyncio
async def test_create_trial_key_success(api_client, mock_service_data):
    user = make_user(trial=0)
    tariff = make_tariff()
    key = make_key(email="trial@123.vpn")

    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)
    mock_service_data.keys.get_data = AsyncMock(return_value=key)

    with patch("api.v1.keys.build_key_services") as mock_build, \
         patch("api.v1.keys.TrialService") as mock_trial_cls:
        mock_create = AsyncMock(return_value={"email": "trial@123.vpn"})
        mock_build.return_value = (MagicMock(proces=mock_create), None, None)
        mock_trial = AsyncMock()
        mock_trial_cls.return_value.installation_trial = mock_trial

        resp = await api_client.post("/api/v1/keys/trial?tg_id=123")

    assert resp.status_code == 200
    mock_trial.assert_called_once()


@pytest.mark.asyncio
async def test_create_trial_key_already_used(api_client, mock_service_data):
    user = make_user(trial=1)
    mock_service_data.users.get_data = AsyncMock(return_value=user)

    resp = await api_client.post("/api/v1/keys/trial?tg_id=123")
    assert resp.status_code == 403
    assert "Trial already used" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_trial_key_user_not_found(api_client, mock_service_data):
    mock_service_data.users.get_data = AsyncMock(return_value=None)

    resp = await api_client.post("/api/v1/keys/trial?tg_id=999")
    assert resp.status_code == 404
