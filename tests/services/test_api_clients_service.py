import hashlib

import pytest

from services.api_clients.service import ApiClientService


class FakeApiClientsPool:
    """Мини-эмуляция asyncpg.Pool для services/api_clients/service.py —
    достаточно точная, чтобы проверить логику (hash/prefix/scopes/revoke/
    rotate), не поднимая реальный Postgres."""

    def __init__(self):
        self._rows = {}
        self._next_id = 1

    async def fetchrow(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO api_clients"):
            name, key_prefix, key_hash, scopes = args
            row = {
                "id": self._next_id,
                "name": name,
                "key_prefix": key_prefix,
                "key_hash": key_hash,
                "scopes": scopes,
                "is_active": True,
                "created_at": None,
                "last_used_at": None,
                "revoked_at": None,
            }
            self._rows[self._next_id] = row
            self._next_id += 1
            return dict(row)

        if q.startswith("UPDATE api_clients SET is_active = FALSE"):
            (client_id,) = args
            row = self._rows.get(client_id)
            if not row:
                return None
            from datetime import datetime, timezone
            row["is_active"] = False
            row["revoked_at"] = datetime.now(timezone.utc)
            return dict(row)

        if "SET key_prefix" in q:
            client_id, key_prefix, key_hash = args
            row = self._rows.get(client_id)
            if not row:
                return None
            row["key_prefix"] = key_prefix
            row["key_hash"] = key_hash
            row["is_active"] = True
            row["revoked_at"] = None
            return dict(row)

        if q.startswith("SELECT") and "WHERE key_hash" in q:
            (key_hash,) = args
            for row in self._rows.values():
                if row["key_hash"] == key_hash and row["is_active"]:
                    return dict(row)
            return None

        raise AssertionError(f"unexpected fetchrow query: {q}")

    async def fetch(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("SELECT") and "ORDER BY id" in q:
            return [dict(r) for r in sorted(self._rows.values(), key=lambda r: r["id"])]
        raise AssertionError(f"unexpected fetch query: {q}")

    async def execute(self, query, *args):
        q = " ".join(query.split())
        if q.startswith("UPDATE api_clients SET last_used_at"):
            (client_id,) = args
            row = self._rows.get(client_id)
            if row:
                from datetime import datetime, timezone
                row["last_used_at"] = datetime.now(timezone.utc)
            return "UPDATE 1"
        raise AssertionError(f"unexpected execute query: {q}")


@pytest.fixture
def pool():
    return FakeApiClientsPool()


@pytest.mark.asyncio
async def test_create_returns_raw_key_matching_hash(pool):
    client, raw_key = await ApiClientService(pool).create("Partner A", ["tariffs:read"])
    assert raw_key.startswith("pub_")
    assert client.key_hash == hashlib.sha256(raw_key.encode()).hexdigest()
    assert client.name == "Partner A"
    assert client.scopes == ["tariffs:read"]
    assert client.is_active is True


@pytest.mark.asyncio
async def test_verify_succeeds_with_correct_key(pool):
    _, raw_key = await ApiClientService(pool).create("Partner A", ["tariffs:read"])
    verified = await ApiClientService(pool).verify(raw_key)
    assert verified is not None
    assert verified.name == "Partner A"


@pytest.mark.asyncio
async def test_verify_fails_with_wrong_key(pool):
    await ApiClientService(pool).create("Partner A", ["tariffs:read"])
    verified = await ApiClientService(pool).verify("pub_not-the-real-key")
    assert verified is None


@pytest.mark.asyncio
async def test_revoke_disables_key(pool):
    client, raw_key = await ApiClientService(pool).create("Partner A", ["tariffs:read"])
    revoked = await ApiClientService(pool).revoke(client.id)
    assert revoked.is_active is False

    verified = await ApiClientService(pool).verify(raw_key)
    assert verified is None, "revoked key must stop authenticating"


@pytest.mark.asyncio
async def test_rotate_invalidates_old_key_and_issues_new_one(pool):
    client, old_key = await ApiClientService(pool).create("Partner A", ["tariffs:read"])
    rotated_client, new_key = await ApiClientService(pool).rotate(client.id)

    assert new_key != old_key
    assert rotated_client.scopes == ["tariffs:read"], "rotate must preserve scopes"

    assert await ApiClientService(pool).verify(old_key) is None
    assert await ApiClientService(pool).verify(new_key) is not None


@pytest.mark.asyncio
async def test_revoke_unknown_id_returns_none(pool):
    assert await ApiClientService(pool).revoke(999) is None


@pytest.mark.asyncio
async def test_rotate_unknown_id_returns_none(pool):
    assert await ApiClientService(pool).rotate(999) is None
