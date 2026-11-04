"""Inbound-set helpers for key creation/renewal.

AVAILABLE_CONNECTIONS (env; [2,3,4,5] in this deployment) is the full set of
inbounds a key is attached to. Subscription keys are created with that set
and stay there — the 3x-ui panel cuts access on its own once its
client's ``expiryTime`` passes, so nothing here toggles inbounds by status.
"""
from config import (
    LIST_AVAILABLE_CONNECTIONS,
    DEFAULT_PRICING_PLAN,
)
from models import Tariff  # noqa: F401  (type hint only)

# Paid overlay (full VPN), filtered to env list.
PAID_OVERLAY_INBOUNDS: list[int] = list(LIST_AVAILABLE_CONNECTIONS)

_TRIAL_TARIFF_ID = int(DEFAULT_PRICING_PLAN)


def env_inbound_ids() -> list[int]:
    """AVAILABLE_CONNECTIONS из .env: seed для пустой таблицы и fallback.

    Читает ``PAID_OVERLAY_INBOUNDS`` в момент вызова (тесты подменяют его).
    """
    seen = set()
    out = []
    for i in PAID_OVERLAY_INBOUNDS:
        if i not in seen:
            seen.add(i)
            out.append(int(i))
    return out


def paid_inbound_ids() -> list[int]:
    """Legacy-алиас env-набора. В продакшене используйте approved_inbound_ids(cache)."""
    return env_inbound_ids()


async def approved_inbound_ids(cache) -> list[int]:
    """Утверждённый набор из кеша БД; env — только если кеш пуст.

    Порядок задаёт ``sort_order`` (первый inbound — primary для ``keys.inbound_id``).
    """
    rows = await cache.approved_inbounds.all()
    if not rows:
        return env_inbound_ids()
    ordered = sorted(
        rows,
        key=lambda r: (
            int(getattr(r, "sort_order", 0) or 0),
            int(r.inbound_id),
        ),
    )
    seen = set()
    out = []
    for row in ordered:
        ib_id = int(row.inbound_id)
        if ib_id not in seen:
            seen.add(ib_id)
            out.append(ib_id)
    return out


def is_subscription(tariff) -> bool:
    """A subscription is a paid tariff OR the trial tariff."""
    if tariff is None:
        return False
    return (getattr(tariff, "amount", 0) or 0) > 0 or int(getattr(tariff, "id", 0)) == _TRIAL_TARIFF_ID
