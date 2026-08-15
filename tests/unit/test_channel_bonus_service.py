import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from models import Key
from services.core.promotions.channel_bonus_service import ChannelBonusService, ChannelBonusResult, PROMO_ID


def _ms(days: int) -> int:
    return days * 24 * 60 * 60 * 1000


def make_key(email="test@vpn.ru", tg_id=123, expiry_offset_days=30):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    return Key(
        tg_id=tg_id,
        client_id="abc123",
        email=email,
        expiry_time=now + _ms(expiry_offset_days),
        key="https://sub.example.com/abc",
        inbound_id=11,
        tariff_id=9,
        name_tariff="Pro",
        used_traffic=1.0,
    )


@pytest.fixture
def mock_conn():
    conn = MagicMock()
    conn.transaction = MagicMock()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    # По умолчанию: бонус ещё не получен (SELECT user_promo_claims → пусто),
    # INSERT флага проходит (возвращается новая строка).
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="UPDATE 1")
    return conn


@pytest.fixture
def mock_pool(mock_conn):
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=mock_conn)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    return pool


@pytest.fixture
def mock_xui():
    xui = MagicMock()
    xui.set_inbounds = AsyncMock(return_value=True)
    xui.extend_client_key = AsyncMock(return_value=True)
    return xui


@pytest.fixture
def mock_service_data():
    sd = MagicMock()
    sd.cache_service = MagicMock()
    sd.cache_service.keys.set = AsyncMock(return_value=None)
    return sd


@pytest.mark.asyncio
async def test_claim_already_claimed(mock_pool, mock_conn, mock_xui, mock_service_data):
    # SELECT user_promo_claims → уже есть запись (бонус получен ранее)
    mock_conn.fetchval = AsyncMock(return_value=1)
    svc = ChannelBonusService(pool=mock_pool, service_data=mock_service_data, cache=mock_service_data.cache_service, xui=mock_xui)

    result = await svc.claim(tg_id=123)

    assert result.status == "already_claimed"
    # Флаг не трогаем (ни INSERT, ни DELETE), ключи не грузим
    mock_conn.fetch.assert_not_called()
    mock_xui.extend_client_key.assert_not_called()


@pytest.mark.asyncio
async def test_claim_race_on_insert(mock_pool, mock_conn, mock_xui, mock_service_data):
    # SELECT говорит "не получен", но параллельный вызов успел вставить флаг —
    # INSERT ... ON CONFLICT DO NOTHING не вернул строку → already_claimed.
    key = make_key(expiry_offset_days=30)
    mock_conn.fetch = AsyncMock(return_value=[dict(key.to_dict())])
    mock_conn.fetchrow = AsyncMock(return_value=None)  # INSERT конфликт
    svc = ChannelBonusService(pool=mock_pool, service_data=mock_service_data, cache=mock_service_data.cache_service, xui=mock_xui)

    result = await svc.claim(tg_id=123)

    assert result.status == "already_claimed"
    mock_xui.extend_client_key.assert_not_called()


@pytest.mark.asyncio
async def test_claim_no_active_keys(mock_pool, mock_conn, mock_xui, mock_service_data):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    expired = Key(
        tg_id=123,
        client_id="abc123",
        email="expired@vpn.ru",
        expiry_time=now - _ms(30),
        key="https://sub.example.com/abc",
        inbound_id=11,
        tariff_id=9,
    )
    mock_conn.fetch = AsyncMock(return_value=[dict(expired.to_dict())])
    svc = ChannelBonusService(pool=mock_pool, service_data=mock_service_data, cache=mock_service_data.cache_service, xui=mock_xui)

    result = await svc.claim(tg_id=123)

    assert result.status == "no_active_keys"
    # Флаг НЕ ставим (и ничего не удаляем — его и не было):
    # пользователь сможет получить бонус после создания ключа.
    delete_calls = [c for c in mock_conn.execute.call_args_list if "DELETE FROM user_promo_claims" in str(c)]
    assert delete_calls == []
    insert_calls = [c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO user_promo_claims" in str(c)]
    assert insert_calls == []


@pytest.mark.asyncio
async def test_claim_single_key_extends(mock_pool, mock_conn, mock_xui, mock_service_data):
    key = make_key(expiry_offset_days=30)
    mock_conn.fetch = AsyncMock(return_value=[dict(key.to_dict())])
    svc = ChannelBonusService(pool=mock_pool, service_data=mock_service_data, cache=mock_service_data.cache_service, xui=mock_xui)

    result = await svc.claim(tg_id=123)

    assert result.status == "claimed"
    assert result.email == "test@vpn.ru"
    assert result.new_expiry_time is not None
    assert result.new_expiry_date is not None

    # Панель продлена до нового expiry_time
    assert mock_xui.extend_client_key.called
    # БД обновлена
    update_call = [c for c in mock_conn.execute.call_args_list if "UPDATE keys" in str(c) and "expiry_time = $1" in str(c)]
    assert len(update_call) == 1


@pytest.mark.asyncio
async def test_claim_multiple_keys_choose(mock_pool, mock_conn, mock_xui, mock_service_data):
    key1 = make_key(email="key1@vpn.ru", expiry_offset_days=30)
    key2 = make_key(email="key2@vpn.ru", expiry_offset_days=60)
    mock_conn.fetch = AsyncMock(return_value=[dict(key1.to_dict()), dict(key2.to_dict())])
    svc = ChannelBonusService(pool=mock_pool, service_data=mock_service_data, cache=mock_service_data.cache_service, xui=mock_xui)

    result = await svc.claim(tg_id=123)

    assert result.status == "choose_key"
    assert len(result.keys) == 2
    assert result.keys[0]["email"] == "key1@vpn.ru"
    assert result.keys[1]["email"] == "key2@vpn.ru"
    # Панель не трогали
    mock_xui.extend_client_key.assert_not_called()
    # Флаг НЕ ставим на этапе выбора ключа — иначе повторный вызов с
    # выбранным email попадал бы в already_claimed без начисления бонуса.
    insert_calls = [c for c in mock_conn.fetchrow.call_args_list if "INSERT INTO user_promo_claims" in str(c)]
    assert insert_calls == []


@pytest.mark.asyncio
async def test_claim_specific_key(mock_pool, mock_conn, mock_xui, mock_service_data):
    key1 = make_key(email="key1@vpn.ru", expiry_offset_days=30)
    key2 = make_key(email="key2@vpn.ru", expiry_offset_days=60)
    mock_conn.fetch = AsyncMock(return_value=[dict(key1.to_dict()), dict(key2.to_dict())])
    svc = ChannelBonusService(pool=mock_pool, service_data=mock_service_data, cache=mock_service_data.cache_service, xui=mock_xui)

    result = await svc.claim(tg_id=123, email="key2@vpn.ru")

    assert result.status == "claimed"
    assert result.email == "key2@vpn.ru"
