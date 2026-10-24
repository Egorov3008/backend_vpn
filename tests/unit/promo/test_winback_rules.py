"""Акция возврата: окно истёкшего ключа, группы и формат кода."""
from datetime import datetime, timezone

from models import Key
from services.core.promo_codes.winback import CODE_PREFIX, WinbackPolicy, generate_code, lapsed_key, pick_cohort

DAY_MS = 86_400_000
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
POLICY = WinbackPolicy(
    enabled=True, min_days=7, max_days=60, discount_percent=30, valid_hours=72,
    max_months=1, repeat_days=90, control_percent=20, attribution_days=7,
)


def _key(expiry_ms: int, email: str = "k") -> Key:
    return Key(tg_id=1, client_id="c", email=email, expiry_time=expiry_ms, key="s", inbound_id=1, tariff_id=1, created_at=0)


def test_latest_key_decides():
    old = _key(NOW_MS - 50 * DAY_MS, "old")
    recent = _key(NOW_MS - 10 * DAY_MS, "recent")
    assert lapsed_key([old, recent], POLICY, NOW).email == "recent"


def test_window_edges():
    assert lapsed_key([_key(NOW_MS - 3 * DAY_MS)], POLICY, NOW) is None
    assert lapsed_key([_key(NOW_MS - 61 * DAY_MS)], POLICY, NOW) is None
    assert lapsed_key([_key(NOW_MS + DAY_MS), _key(NOW_MS - 10 * DAY_MS)], POLICY, NOW) is None
    assert lapsed_key([], POLICY, NOW) is None


def test_cohort_is_stable_and_roughly_split():
    assert pick_cohort(42, 20) == pick_cohort(42, 20)
    control = sum(pick_cohort(i, 20) == "control" for i in range(10_000))
    assert 1_500 < control < 2_500
    assert pick_cohort(42, 0) == "offer" and pick_cohort(42, 100) == "control"


def test_code_format_is_valid_promo_code():
    from services.core.promo_codes.service import CODE_PATTERN

    code = generate_code()
    assert code.startswith(CODE_PREFIX) and CODE_PATTERN.match(code)
