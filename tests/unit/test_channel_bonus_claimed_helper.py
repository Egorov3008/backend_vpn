import pytest
from unittest.mock import AsyncMock, MagicMock

from services.core.promotions.channel_bonus_service import is_channel_bonus_claimed


@pytest.mark.asyncio
async def test_claimed_true_when_row_exists():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"1": 1})
    assert await is_channel_bonus_claimed(pool, 123) is True
    pool.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_claimed_false_when_no_row():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    assert await is_channel_bonus_claimed(pool, 123) is False


@pytest.mark.asyncio
async def test_claimed_false_on_db_error():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
    assert await is_channel_bonus_claimed(pool, 123) is False


@pytest.mark.asyncio
async def test_claimed_uses_channel_subscription_bonus_promo_id():
    """promo_id передаётся как параметр (channel_subscription_bonus)."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    await is_channel_bonus_claimed(pool, 999)
    # fetchrow(sql, tg_id, promo_id)
    assert pool.fetchrow.call_args.args[1] == 999
    assert pool.fetchrow.call_args.args[2] == "channel_subscription_bonus"