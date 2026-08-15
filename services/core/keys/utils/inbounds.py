"""Inbound-set helpers for key creation/renewal.

XUI_INBOUND_ID_LANDING (7) is the landing-only inbound (anonymous 24h keys,
never a paid subscription's inbound set — see BASELINE_INBOUNDS below).
AVAILABLE_CONNECTIONS (env; [2,3,4,5] in this deployment) is the full set of
inbounds a paid/trial key is attached to. Subscription keys are created with
that set and stay there — the 3x-ui panel cuts access on its own once its
client's ``expiryTime`` passes, so nothing here toggles inbounds by status.
"""
from config import (
    LIST_AVAILABLE_CONNECTIONS,
    settings,
    DEFAULT_PRICING_PLAN,
)
from models import Tariff  # noqa: F401  (type hint only)

# Landing-only inbound. Used to detect not-yet-upgraded landing keys
# (see KeyCreationService._find_landing_origin_key) — NOT part of a paid
# key's inbound set (see paid_inbound_ids below).
BASELINE_INBOUNDS: list[int] = (
    [settings.xui_inbound_id_landing] if settings.xui_inbound_id_landing else []
)
# Paid overlay (full VPN), filtered to env list.
PAID_OVERLAY_INBOUNDS: list[int] = list(LIST_AVAILABLE_CONNECTIONS)

_TRIAL_TARIFF_ID = int(DEFAULT_PRICING_PLAN)


def paid_inbound_ids() -> list[int]:
    """active/trial: paid overlay only (dedup, preserve order).

    Does NOT include BASELINE_INBOUNDS (landing inbound) — a paid/trial
    key's inbound set is exactly AVAILABLE_CONNECTIONS.
    """
    seen = set()
    out = []
    for i in PAID_OVERLAY_INBOUNDS:
        if i not in seen:
            seen.add(i)
            out.append(int(i))
    return out


def is_subscription(tariff) -> bool:
    """A subscription is a paid tariff OR the trial tariff."""
    if tariff is None:
        return False
    return (getattr(tariff, "amount", 0) or 0) > 0 or int(getattr(tariff, "id", 0)) == _TRIAL_TARIFF_ID
