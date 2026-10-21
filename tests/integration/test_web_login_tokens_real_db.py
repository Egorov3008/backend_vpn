"""Интеграционный тест: PostgresWebLoginTokenStore против живой БД.

Проверяет условные переходы статуса (pending → confirmed → used) и истечение.
База и запуск — см. tests/integration/conftest.py.
"""
import asyncio

from services.web_auth.service import ConfirmStatus, PollStatus, WebLoginService
from services.web_auth.store import PostgresWebLoginTokenStore


async def test_full_flow_on_real_db(pool):
    service = WebLoginService(PostgresWebLoginTokenStore(pool), ttl_seconds=600)
    started = await service.start(landing_uid="uid1")

    assert (await service.complete(started.poll_secret)).status == PollStatus.PENDING
    confirmed = await service.confirm(started.token, 555)
    assert confirmed.status == ConfirmStatus.CONFIRMED and confirmed.landing_uid == "uid1"
    assert (await service.confirm(started.token, 555)).status == ConfirmStatus.ALREADY_CONFIRMED
    assert (await service.confirm(started.token, 666)).status == ConfirmStatus.EXPIRED

    done = await service.complete(started.poll_secret)
    assert done.status == PollStatus.CONFIRMED and done.tg_id == 555
    assert (await service.complete(started.poll_secret)).status == PollStatus.EXPIRED

    raw = await pool.fetchval("SELECT count(*) FROM web_login_tokens WHERE token_hash = $1", started.token)
    assert raw == 0  # хранится только хеш


async def test_concurrent_complete_issues_one_session(pool):
    service = WebLoginService(PostgresWebLoginTokenStore(pool), ttl_seconds=600)
    started = await service.start()
    await service.confirm(started.token, 555)
    results = await asyncio.gather(*[service.complete(started.poll_secret) for _ in range(5)])
    assert sum(r.status == PollStatus.CONFIRMED for r in results) == 1


async def test_expired_token_cannot_be_confirmed(pool):
    service = WebLoginService(PostgresWebLoginTokenStore(pool), ttl_seconds=600)
    started = await service.start()
    await pool.execute("UPDATE web_login_tokens SET expires_at = NOW() - INTERVAL '1 second'")
    assert (await service.confirm(started.token, 555)).status == ConfirmStatus.EXPIRED
    assert (await service.complete(started.poll_secret)).status == PollStatus.EXPIRED


async def test_old_tokens_cleaned_on_create(pool):
    service = WebLoginService(PostgresWebLoginTokenStore(pool), ttl_seconds=600)
    await service.start()
    await pool.execute("UPDATE web_login_tokens SET expires_at = NOW() - INTERVAL '2 days'")
    await service.start()
    assert await pool.fetchval("SELECT count(*) FROM web_login_tokens") == 1
