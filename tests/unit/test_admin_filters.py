from types import SimpleNamespace

from services.admin_filters import EXPIRING_WINDOW_MS, filter_keys, filter_payments, filter_users

NOW = 1_800_000_000_000
DAY = 24 * 3600 * 1000


def user(tg_id, username=None, first_name=None):
    return SimpleNamespace(tg_id=tg_id, username=username, first_name=first_name, last_name=None)


def key(email, tg_id, expiry):
    return SimpleNamespace(email=email, tg_id=tg_id, expiry_time=expiry)


USERS = [user(123456, "ivan"), user(777001, None, "Пётр"), user(42, "Anna")]
KEYS = [
    key("ivan_k1", 1, NOW + 30 * DAY),
    key("ivan_k2", 1, NOW + DAY),
    key("anna_k1", 2, NOW - DAY),
    key("edge", 2, NOW + EXPIRING_WINDOW_MS),
]


def test_users_without_query_returns_all():
    assert filter_users(USERS) == USERS
    assert filter_users(USERS, "  ") == USERS


def test_users_by_tg_id_part():
    assert [u.tg_id for u in filter_users(USERS, "7700")] == [777001]


def test_users_by_username_and_name_case_insensitive():
    assert [u.tg_id for u in filter_users(USERS, "@IVAN")] == [123456]
    assert [u.tg_id for u in filter_users(USERS, "пёт")] == [777001]


def test_keys_by_status():
    def emails(status):
        return [k.email for k in filter_keys(KEYS, now_ms=NOW, status=status)]

    assert emails("active") == ["ivan_k1", "ivan_k2", "edge"]
    assert emails("expiring") == ["ivan_k2", "edge"]
    assert emails("expired") == ["anna_k1"]


def test_keys_by_owner_and_email_part():
    assert [k.email for k in filter_keys(KEYS, now_ms=NOW, tg_id=1, q="K2")] == ["ivan_k2"]


def test_payments_by_owner_and_status():
    payments = [
        SimpleNamespace(tg_id=1, status="succeeded"),
        SimpleNamespace(tg_id=1, status="pending"),
        SimpleNamespace(tg_id=2, status="succeeded"),
    ]
    assert filter_payments(payments, tg_id=1, status="succeeded") == [payments[0]]
    assert len(filter_payments(payments)) == 3
