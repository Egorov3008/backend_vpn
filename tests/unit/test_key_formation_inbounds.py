"""Формирование ключа не должно проходить с пустым набором inbound'ов."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.core.keys.utils.formtion import FormationKey


def _deps(approved_rows, server_inbound_ids=None):
    cache = MagicMock()
    cache.approved_inbounds.all = AsyncMock(return_value=list(approved_rows))
    cache.keys.all = AsyncMock(return_value=[])

    connected_data = MagicMock()
    connected_data.data = AsyncMock(
        return_value={
            "subscription_url": "https://sub.example",
            "inbound_ids": list(server_inbound_ids or []),
        }
    )
    expiry = MagicMock()
    expiry.key_duration_new_key = MagicMock(return_value=1_800_000_000_000)
    return FormationKey(cache=cache, connected_data=connected_data, expiry=expiry), cache


def _tariff(amount: float) -> MagicMock:
    return MagicMock(
        id=7,
        amount=amount,
        period=30,
        limit_ip=1,
        name_tariff="Стандарт",
    )


@pytest.mark.asyncio
async def test_paid_tariff_without_approved_inbounds_returns_none():
    formation, _ = _deps(approved_rows=[])

    key = await formation.form_new_key(tg_id=1, tariff=_tariff(199.0), server_id=1)

    assert key is None


@pytest.mark.asyncio
async def test_non_subscription_without_server_inbounds_returns_none():
    formation, _ = _deps(approved_rows=[SimpleNamespace(inbound_id=11, sort_order=0)])

    # amount=0 и id != trial -> не подписка, набор берётся из server_data
    key = await formation.form_new_key(tg_id=1, tariff=_tariff(0.0), server_id=1)

    assert key is None
