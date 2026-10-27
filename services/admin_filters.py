"""Серверные фильтры админ-списков (`GET /admin/users|keys|payments`).

Списки берутся из кеша (`CacheService`), поэтому фильтры — чистые функции над
уже загруженными объектами: их легко тестировать, а эндпоинты только
применяют фильтр до пагинации, чтобы `X-Total-Count` считал отфильтрованное.
"""
from typing import Iterable, List, Literal, Optional

from models.keys.key import Key
from models.payments.payment import PaymentModel
from models.users.user import User

KeyStatus = Literal["active", "expiring", "expired"]

# «Истекает»: осталось не больше 3 суток (как в воронке напоминаний бота).
EXPIRING_WINDOW_MS = 3 * 24 * 3600 * 1000


def filter_users(users: Iterable[User], q: Optional[str] = None) -> List[User]:
    """``q`` из цифр ищет по части ``tg_id``, иначе по username и имени."""
    users = list(users)
    query = (q or "").strip().lstrip("@").lower()
    if not query:
        return users
    if query.isdigit():
        return [u for u in users if query in str(u.tg_id)]
    return [
        u for u in users
        if any(query in (value or "").lower() for value in (u.username, u.first_name, u.last_name))
    ]


def key_status(key: Key, now_ms: int) -> KeyStatus:
    if key.expiry_time <= now_ms:
        return "expired"
    if key.expiry_time <= now_ms + EXPIRING_WINDOW_MS:
        return "expiring"
    return "active"


def filter_keys(
    keys: Iterable[Key],
    *,
    now_ms: int,
    tg_id: Optional[int] = None,
    q: Optional[str] = None,
    status: Optional[KeyStatus] = None,
) -> List[Key]:
    """``status=active`` — все действующие ключи, включая истекающие."""
    query = (q or "").strip().lower()
    result = []
    for key in keys:
        if tg_id is not None and key.tg_id != tg_id:
            continue
        if query and query not in key.email.lower():
            continue
        if status is not None:
            current = key_status(key, now_ms)
            if status == "active" and current == "expired":
                continue
            if status != "active" and current != status:
                continue
        result.append(key)
    return result


def filter_payments(
    payments: Iterable[PaymentModel],
    *,
    tg_id: Optional[int] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
) -> List[PaymentModel]:
    """``q`` ищет по части ID платежа YooKassa (без учёта регистра)."""
    query = (q or "").strip().lower()
    return [
        p for p in payments
        if (tg_id is None or p.tg_id == tg_id)
        and (status is None or p.status == status)
        and (not query or query in (p.payment_id or "").lower())
    ]
