"""Предохранитель от двойной выдачи ключа по одному платежу."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models.keys.key import Key
from services.core.payment.creation_service import KeyCreationService


def make_service(existing_key):
    processor = MagicMock()
    processor.tg_id = 552810834
    processor._conn = MagicMock()
    processor._model_service.keys.service.get = AsyncMock(return_value=existing_key)
    processor._model_service.tariffs.get_data = AsyncMock(return_value=MagicMock(amount=199, period=30))

    create_key = MagicMock()
    create_key._get_days = MagicMock(return_value=29)
    create_key.proces = AsyncMock(return_value={"public_link": "new", "days": 30, "link_to_connect": "new", "email": "new"})

    service = KeyCreationService(processor=processor, create_key=create_key)
    return service, create_key


def make_key(email: str, payment_id: str) -> Key:
    return Key(
        tg_id=552810834,
        client_id="cid",
        email=email,
        expiry_time=1794340318317,
        key=f"https://tds-pro.space:2096/TolkoDlyaSv0ih_Bot/{email}",
        inbound_id=14,
        payment_id=payment_id,
    )


@pytest.mark.asyncio
async def test_existing_key_for_payment_is_returned_not_recreated():
    existing = make_key("3dmkbt", "pay_1")
    service, create_key = make_service(existing)

    result = await service.process(tariff_id="2", payment_id="pay_1")

    create_key.proces.assert_not_called()
    assert result["already_created"] is True
    assert result["email"] == "3dmkbt"
    assert result["link_to_connect"] == existing.key


@pytest.mark.asyncio
async def test_no_existing_key_creates_one_with_payment_id():
    service, create_key = make_service(None)

    result = await service.process(tariff_id="2", payment_id="pay_2")

    create_key.proces.assert_awaited_once()
    assert create_key.proces.await_args.kwargs["payment_id"] == "pay_2"
    assert "already_created" not in result


def test_key_persists_payment_id():
    key = make_key("sq91yr", "pay_3")
    assert key.to_dict()["payment_id"] == "pay_3"
    assert Key.from_dict(key.to_dict()).payment_id == "pay_3"
