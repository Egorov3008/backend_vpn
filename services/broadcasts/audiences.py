"""Кому слать рассылку. Один класс — один сегмент; новый сегмент = новый класс в AUDIENCES.

Каждый сегмент — условие на строку ``users u``. Гости лендинга (tg_id < 0) не
получают ничего: у них нет чата с ботом.
"""
from typing import Optional, Protocol

_NOW_MS = "(EXTRACT(EPOCH FROM NOW()) * 1000)::bigint"
_HAS_ACTIVE_KEY = f"EXISTS (SELECT 1 FROM keys k WHERE k.tg_id = u.tg_id AND k.expiry_time > {_NOW_MS})"
_HAS_PAID = "EXISTS (SELECT 1 FROM payments p WHERE p.tg_id = u.tg_id AND p.status = 'succeeded')"


class Audience(Protocol):
    id: str
    title: str

    def condition(self) -> str:
        """SQL-условие на ``users u`` (без параметров)."""
        ...


class AllUsers:
    id = "all"
    title = "Все пользователи"

    def condition(self) -> str:
        return "TRUE"


class WithActiveKey:
    id = "active_key"
    title = "С действующим ключом"

    def condition(self) -> str:
        return _HAS_ACTIVE_KEY


class WithoutActiveKey:
    id = "no_active_key"
    title = "Без действующего ключа"

    def condition(self) -> str:
        return f"NOT {_HAS_ACTIVE_KEY}"


class Paid:
    id = "paid"
    title = "Хоть раз платили"

    def condition(self) -> str:
        return _HAS_PAID


class NeverPaid:
    id = "never_paid"
    title = "Ни разу не платили"

    def condition(self) -> str:
        return f"NOT {_HAS_PAID}"


AUDIENCES: tuple[Audience, ...] = (AllUsers(), WithActiveKey(), WithoutActiveKey(), Paid(), NeverPaid())


def find_audience(audience_id: str) -> Optional[Audience]:
    return next((a for a in AUDIENCES if a.id == audience_id), None)


def recipients_sql(audience: Audience) -> str:
    """SELECT tg_id получателей сегмента; ``$1`` — tg_id, которого исключить (админ, ему уходит контрольная копия)."""
    return (
        "SELECT u.tg_id FROM users u"
        f" WHERE u.tg_id > 0 AND u.tg_id IS DISTINCT FROM $1::bigint AND ({audience.condition()})"
    )
