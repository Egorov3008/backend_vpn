"""Inbound-set helpers for the grace model.

XUI_INBOUND_ID_LANDING (7) is the always-on baseline inbound.
AVAILABLE_CONNECTIONS (env; [2,3,4,5] in this deployment) is the paid overlay, toggled by subscription state.
"""
from config import (
    LIST_AVAILABLE_CONNECTIONS,
    settings,
    GRACE_PERIOD_DAYS,
    DEFAULT_PRICING_PLAN,
)
from models import Tariff  # noqa: F401  (type hint only)

# Always-on baseline (telegram). Empty if landing inbound not configured.
BASELINE_INBOUNDS: list[int] = (
    [settings.xui_inbound_id_landing] if settings.xui_inbound_id_landing else []
)
# Paid overlay (full VPN), filtered to env list.
PAID_OVERLAY_INBOUNDS: list[int] = list(LIST_AVAILABLE_CONNECTIONS)

GRACE_PERIOD_MS: int = GRACE_PERIOD_DAYS * 86_400_000

_TRIAL_TARIFF_ID = int(DEFAULT_PRICING_PLAN)


def paid_inbound_ids() -> list[int]:
    """active/trial: baseline + paid overlay (dedup, preserve order)."""
    seen = set()
    out = []
    for i in BASELINE_INBOUNDS + PAID_OVERLAY_INBOUNDS:
        if i not in seen:
            seen.add(i)
            out.append(int(i))
    return out


def grace_inbound_ids() -> list[int]:
    """grace: baseline only (telegram)."""
    return list(BASELINE_INBOUNDS)


def expired_inbound_ids() -> list[int]:
    """expired: no inbounds (client disabled/deleted)."""
    return []


def expected_inbound_ids(status: str) -> list[int]:
    if status == "active":
        return paid_inbound_ids()
    if status == "grace":
        return grace_inbound_ids()
    return expired_inbound_ids()


def is_subscription(tariff) -> bool:
    """A subscription is a paid tariff OR the trial tariff (both get grace)."""
    if tariff is None:
        return False
    return (getattr(tariff, "amount", 0) or 0) > 0 or int(getattr(tariff, "id", 0)) == _TRIAL_TARIFF_ID
