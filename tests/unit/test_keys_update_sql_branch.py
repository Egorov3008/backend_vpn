"""Регрессия: BaseRepository.update для таблицы keys.

Корень бага: при переносе владельца ключа (claim_key) update шёл по UPSERT
(INSERT ... ON CONFLICT (tg_id, client_id)) даже когда WHERE был по email.
После смены tg_id конфликт по (tg_id, client_id) не находил строку → INSERT
новой строки с тем же email → UniqueViolationError на uq_keys_email.

Фикс: UPSERT по (tg_id, client_id) применяется только при апдейте по
tg_id/client_id. При апдейте по email — обычный UPDATE WHERE email = $1.

Тест проверяет выбор SQL-ветки без реальной БД (fake conn фиксирует запрос).
"""
import pytest

from database.base import BaseRepository
from models.keys.key import Key


class _FakeConn:
    """Записывает SQL и параметры, переданные в execute()."""

    def __init__(self):
        self.query = None
        self.values = None

    async def execute(self, query, *values):
        self.query = query
        self.values = values
        return "UPDATE 1"


def _make_key() -> Key:
    return Key(
        tg_id=7563318767,
        client_id="client-xyz",
        email="ov8egq",
        expiry_time=1730000000000,
        key="vless://...",
        inbound_id=10,
        tariff_id=10,
        converted_tg_id=7563318767,
    )


@pytest.mark.asyncio
async def test_keys_update_by_email_uses_plain_update():
    """Апдейт по email → обычный UPDATE, без INSERT/ON CONFLICT.

    Воспроизводит баг UniqueViolation: при переносе tg_id UPSERT вставил бы
    новую строку с уже занятым email. Должен быть UPDATE WHERE email = $1.
    """
    repo = BaseRepository(table_name="keys", model=Key)
    conn = _FakeConn()

    key = _make_key()
    key.tg_id = 7563318767  # перенос владельца: pseudo(<0) → реальный

    await repo.update(conn, {"email": key.email}, **key.to_dict())

    assert conn.query is not None
    q = conn.query.strip().upper()
    assert q.startswith("UPDATE KEYS"), f"expected UPDATE, got: {conn.query!r}"
    assert "ON CONFLICT" not in q, f"unexpected UPSERT: {conn.query!r}"
    assert "INSERT INTO" not in q, f"unexpected INSERT: {conn.query!r}"
    assert "WHERE EMAIL = $1" in q


@pytest.mark.asyncio
async def test_keys_update_by_tg_id_keeps_upsert():
    """Апдейт по tg_id → сохраняется UPSERT по (tg_id, client_id).

    Гонки при создании ключей обрабатываются именно UPSERT-веткой; фикс не
    должен её ломать.
    """
    repo = BaseRepository(table_name="keys", model=Key)
    conn = _FakeConn()

    key = _make_key()

    await repo.update(conn, {"tg_id": key.tg_id}, **key.to_dict())

    assert conn.query is not None
    q = conn.query.strip().upper()
    assert q.startswith("INSERT INTO KEYS"), f"expected UPSERT, got: {conn.query!r}"
    assert "ON CONFLICT (TG_ID, CLIENT_ID)" in q


@pytest.mark.asyncio
async def test_keys_update_by_email_includes_changed_tg_id():
    """Главная регрессия: при переносе tg_id новый tg_id попадает в SET."""
    repo = BaseRepository(table_name="keys", model=Key)
    conn = _FakeConn()

    key = _make_key()
    key.tg_id = 7563318767  # перенос владельца

    await repo.update(conn, {"email": key.email}, **key.to_dict())

    # tg_id должен быть среди значений SET (в typed_values после where_value),
    # но НЕ дублироваться в WHERE (where_value — email).
    assert conn.values is not None
    assert conn.values[0] == "ov8egq"  # WHERE email = $1
    assert 7563318767 in conn.values  # SET tg_id = $N