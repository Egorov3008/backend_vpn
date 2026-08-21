"""
Unit-тесты для _attach_referral_if_any (#8 — атомарная запись referral_id).

Раньше был read-check-write (TOCTOU): два concurrent claim для одного
referred_tg_id с разными реферерами оба видели referral_id=None и оба
перезаписывали его, расходясь с referral_redemptions. Теперь UPDATE ... WHERE
referral_id IS NULL RETURNING — выигрывает ровно один запрос.
"""
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from api.v1.landing import _attach_referral_if_any


def _pool_with_fetchrow(fetchrow_return):
    """asyncpg.Pool mock: pool.acquire() → conn с заданным fetchrow."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def _service_data(*, referrer_tg_id=100, link_id=5, user=None):
    sd = MagicMock()
    link = MagicMock(referrer_tg_id=referrer_tg_id, id=link_id)
    sd.referral_links.get_by = AsyncMock(return_value=link)
    sd.users.get_data = AsyncMock(return_value=user if user is not None else MagicMock(tg_id=200, referral_id=None))
    sd.data_service.referral_redemptions.create = AsyncMock()
    return sd


def _cache():
    cache = MagicMock()
    cache.users.set = AsyncMock()
    return cache


@pytest.mark.asyncio
async def test_first_call_wins_and_merges():
    user = MagicMock(tg_id=200, referral_id=None)
    sd = _service_data(user=user)
    cache = _cache()
    pool, conn = _pool_with_fetchrow({"referral_id": 100})

    ok = await _attach_referral_if_any(
        pool, sd, cache, tg_ref=None, referred_tg_id=200,
        raw_ref_token="ref_abc123def456",
    )

    assert ok == 100
    # атомарный UPDATE выполнен
    conn.fetchrow.assert_awaited_once()
    query = conn.fetchrow.await_args.args[0]
    assert "referral_id IS NULL" in query, "UPDATE должен guards на referral_id IS NULL"
    assert conn.fetchrow.await_args.args[1] == 100  # referrer_tg_id
    assert conn.fetchrow.await_args.args[2] == 200  # referred_tg_id
    # кэш обновлён актуальным referral_id
    assert user.referral_id == 100
    cache.users.set.assert_awaited_once()
    # redemption создан
    sd.data_service.referral_redemptions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_concurrent_call_loses_no_overwrite():
    """UPDATE вернул None (referral_id уже выставлен другим запросом) → no-op."""
    user = MagicMock(tg_id=200, referral_id=None)
    sd = _service_data(user=user)
    cache = _cache()
    pool, conn = _pool_with_fetchrow(None)  # проиграли гонку

    ok = await _attach_referral_if_any(
        pool, sd, cache, tg_ref=None, referred_tg_id=200,
        raw_ref_token="ref_abc123def456",
    )

    assert ok is None
    # кэш не трогаем, redemption не создаём
    cache.users.set.assert_not_awaited()
    sd.data_service.referral_redemptions.create.assert_not_awaited()
    # и не перезаписываем referral_id в объекте пользователя
    assert user.referral_id is None


@pytest.mark.asyncio
async def test_self_referral_skipped_before_update():
    """referrer == referred → отсекаем до UPDATE (self-referral guard)."""
    sd = _service_data(referrer_tg_id=200)
    cache = _cache()
    pool, conn = _pool_with_fetchrow({"referral_id": 200})

    ok = await _attach_referral_if_any(
        pool, sd, cache, tg_ref=None, referred_tg_id=200,
        raw_ref_token="ref_abc123def456",
    )

    assert ok is None
    conn.fetchrow.assert_not_awaited()  # UPDATE не выполнялся
    sd.data_service.referral_redemptions.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_ref_token_returns_false():
    sd = _service_data()
    sd.referral_links.get_by = AsyncMock(return_value=None)
    cache = _cache()
    pool, conn = _pool_with_fetchrow({"referral_id": 100})

    ok = await _attach_referral_if_any(
        pool, sd, cache, tg_ref=None, referred_tg_id=200,
        raw_ref_token="ref_abc123def456",
    )

    assert ok is None
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_redemption_is_idempotent():
    """UNIQUE(referred_tg_id): повторный insert redemption → UniqueViolation,
    ловится, merge считается выполненным (referral_id уже наш)."""
    user = MagicMock(tg_id=200, referral_id=None)
    sd = _service_data(user=user)
    sd.data_service.referral_redemptions.create = AsyncMock(
        side_effect=asyncpg.UniqueViolationError()
    )
    cache = _cache()
    pool, conn = _pool_with_fetchrow({"referral_id": 100})

    ok = await _attach_referral_if_any(
        pool, sd, cache, tg_ref=None, referred_tg_id=200,
        raw_ref_token="ref_abc123def456",
    )

    assert ok == 100
    cache.users.set.assert_awaited_once()  # referral_id записан


@pytest.mark.asyncio
async def test_cookie_path_takes_precedence_over_body_token():
    """tg_ref cookie имеет приоритет над raw_ref_token."""
    from api.v1.landing import _sign_ref_cookie

    user = MagicMock(tg_id=200, referral_id=None)
    sd = _service_data(user=user)
    cache = _cache()
    pool, conn = _pool_with_fetchrow({"referral_id": 100})

    cookie = _sign_ref_cookie("ref_abc123def456")
    ok = await _attach_referral_if_any(
        pool, sd, cache, tg_ref=cookie, referred_tg_id=200,
        raw_ref_token="ref_deadbeef0000",  # другой токен в теле — должен проиграть
    )

    assert ok == 100
    # lookup шёл по токену из куки, не из тела
    sd.referral_links.get_by.assert_awaited_once()
    token_used = sd.referral_links.get_by.await_args.kwargs["token"]
    assert token_used == "ref_abc123def456"