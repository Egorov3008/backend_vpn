"""
Regression test: PaymentRouter factory must wire ReferralBonusService.

Bug: build_payment_router previously returned a PaymentRouter with
bonus_service=None, so successful payments never triggered referral bonuses.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.factories import build_payment_router
from services.core.referral.bonus_service import ReferralBonusService


@pytest.mark.asyncio
async def test_build_payment_router_wires_referral_bonus_service():
    """Factory must pass a ReferralBonusService instance to PaymentRouter."""
    pool = MagicMock()
    service_data = MagicMock()
    service_data.users = MagicMock()
    service_data.data_service = MagicMock()
    service_data.keys = MagicMock()
    cache = MagicMock()
    data_service = MagicMock()

    router = build_payment_router(
        pool=pool,
        service_data=service_data,
        cache=cache,
        data_service=data_service,
    )

    assert router.bonus_service is not None, (
        "PaymentRouter.bonus_service must be set by build_payment_router"
    )
    assert isinstance(router.bonus_service, ReferralBonusService), (
        "bonus_service must be an instance of ReferralBonusService"
    )
    # #6: бонус-сервис должен получать xui_session и cache, чтобы продлевать
    # ключ реферала в 3x-UI панели + DB + cache согласованно.
    assert router.bonus_service._xui is not None, (
        "bonus_service must be wired with xui_session (panel expiry on +3 days)"
    )
    assert router.bonus_service._cache is cache, (
        "bonus_service must reuse the same CacheService instance passed to the factory"
    )
