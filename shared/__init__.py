"""
Shared configuration for VPN platform bot and backend.

Centralises settings that are used in both bot and backend:
- Database connection
- Bot secret key (service-to-service auth)
- YooKassa payment settings
- Trial / limits / discounts
- Referral bonus percentages

Both bot/ and backend/ can import from this package to keep
configuration in one place.
"""
from shared.config.core import (
    CoreSettings,
    core_settings,
    REFERRAL_BONUS_PERCENTAGES,
)

__all__ = [
    "CoreSettings",
    "core_settings",
    "REFERRAL_BONUS_PERCENTAGES",
]
