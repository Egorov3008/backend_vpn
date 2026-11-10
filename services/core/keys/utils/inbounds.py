"""Inbound-set helpers for key creation/renewal.

The approved inbound set lives in the ``approved_inbounds`` table and is
mirrored into ``CacheService.approved_inbounds``. Subscription keys are
created with that set and stay there — the 3x-ui panel cuts access on its
own once its client's ``expiryTime`` passes, so nothing here toggles
inbounds by status.
"""
from config import DEFAULT_PRICING_PLAN
from models import Tariff  # noqa: F401  (type hint only)

_TRIAL_TARIFF_ID = int(DEFAULT_PRICING_PLAN)


async def approved_inbound_ids(cache) -> list[int]:
    """Утверждённый набор inbound'ов из кеша таблицы ``approved_inbounds``.

    Порядок задаёт ``sort_order`` (первый inbound — primary для ``keys.inbound_id``).
    Пустой список означает, что ни один inbound ещё не утверждён (или кеш не
    загрузился): создавать ключ в таком состоянии нельзя, вызывающий код
    обязан обработать пустой набор.
    """
    rows = await cache.approved_inbounds.all()
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
