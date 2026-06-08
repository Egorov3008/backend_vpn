"""Shared configuration package."""

from shared.config.core import (
    CoreSettings,
    core_settings,
    REFERRAL_BONUS_PERCENTAGES,
    get_core_settings,
)

__all__ = [
    "CoreSettings",
    "core_settings",
    "REFERRAL_BONUS_PERCENTAGES",
    "get_core_settings",
]
