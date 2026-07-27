import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from models import Key, User
from services.notifications.funnels.channel_bonus_reminder import (
    ChannelBonusReminderFunnel,
)


def _ms(days: int) -> int:
    return days * 24 * 60 * 60 * 1000


def make_key(email="a@x.c", tg_id=123, created_days_ago=10, expiry_offset_days=30, grace_offset_days=37):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    return Key(
        tg_id=tg_id,
        client_id="abc",
        email=email,
        expiry_time=now + _ms(expiry_offset_days),
        key="https://sub.example.com/abc",
        inbound_id=11,
        tariff_id=9,
        created_at=now - _ms(created_days_ago),
        grace_expiry=now + _ms(grace_offset_days),
    )


def make_user(tg_id=123, is_blocked=False):
    return User(tg_id=tg_id, is_blocked=is_blocked)


def make_ctx(keys, tg_id=123):
    user = make_user(tg_id=tg_id)
    return MagicMock(user=user, keys=keys, segment_keys=[])


@pytest.fixture
def pool():
    p = MagicMock()
    p.fetchrow = AsyncMock(return_value=None)
    return p


@pytest.fixture
def funnel(pool):
    rl = MagicMock()
    rl.acquire = AsyncMock(return_value=None)
    f = ChannelBonusReminderFunnel(pool=pool, rate_limiter=rl)
    f._state = MagicMock()
    f._state.get_state = AsyncMock(return_value=None)
    f._state.advance_step = AsyncMock(return_value=1)
    f._state.mark_terminal = AsyncMock(return_value=None)
    return f


# ---------- should_send ----------


@pytest.mark.asyncio
async def test_should_send_no_active_key(funnel):
    """Только expired-ключ → False."""
    k = make_key()
    k.expiry_time = 0
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s:
        s.canall_url = "https://t.me/chan"
        s.channel_bonus_days = 7
    assert await funnel.should_send(make_ctx([k])) is False


@pytest.mark.asyncio
async def test_should_send_bonus_claimed_marks_terminal(funnel, pool):
    """Бонус получен → False + mark_terminal."""
    k = make_key(created_days_ago=10)
    pool.fetchrow = AsyncMock(return_value={"1": 1})
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s, \
         patch("services.notifications.funnels.channel_bonus_reminder.is_channel_bonus_claimed", new=AsyncMock(return_value=True)):
        s.canall_url = "https://t.me/chan"
        s.channel_bonus_days = 7
        assert await funnel.should_send(make_ctx([k])) is False
    funnel._state.mark_terminal.assert_awaited_once()


@pytest.mark.asyncio
async def test_should_send_key_too_young(funnel, pool):
    """Ключ создан < 24ч → False (рано для шага 1)."""
    k = make_key(created_days_ago=0)
    k.created_at = int(datetime.now(timezone.utc).timestamp() * 1000) - 3_600_000
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s, \
         patch("services.notifications.funnels.channel_bonus_reminder.is_channel_bonus_claimed", new=AsyncMock(return_value=False)):
        s.canall_url = "https://t.me/chan"
        s.channel_bonus_days = 7
        assert await funnel.should_send(make_ctx([k])) is False


@pytest.mark.asyncio
async def test_should_send_due_not_subscribed_true(funnel, pool):
    """Старый ключ, step 0, не подписан → True."""
    k = make_key(created_days_ago=10)
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s, \
         patch("services.notifications.funnels.channel_bonus_reminder.is_channel_bonus_claimed", new=AsyncMock(return_value=False)), \
         patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        s.canall_url = "https://t.me/chan"
        s.channel_bonus_days = 7
        b.get_chat_member = AsyncMock(return_value={"status": "left"})
        assert await funnel.should_send(make_ctx([k])) is True


@pytest.mark.asyncio
async def test_should_send_subscribed_false(funnel, pool):
    """Подписан (status=member) → False."""
    k = make_key(created_days_ago=10)
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s, \
         patch("services.notifications.funnels.channel_bonus_reminder.is_channel_bonus_claimed", new=AsyncMock(return_value=False)), \
         patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        s.canall_url = "https://t.me/chan"
        s.channel_bonus_days = 7
        b.get_chat_member = AsyncMock(return_value={"status": "member"})
        assert await funnel.should_send(make_ctx([k])) is False


@pytest.mark.asyncio
async def test_should_send_canall_url_empty_disabled(funnel):
    """CANALL_URL не задан → False (disabled), без вызова API."""
    k = make_key(created_days_ago=10)
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s, \
         patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        s.canall_url = ""
        s.channel_bonus_days = 7
        b.get_chat_member = AsyncMock(return_value={"status": "left"})
        assert await funnel.should_send(make_ctx([k])) is False
        b.get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_should_send_get_chat_member_none_skips(funnel, pool):
    """get_chat_member вернул None (сеть) → False (skip цикла)."""
    k = make_key(created_days_ago=10)
    pool.fetchrow = AsyncMock(return_value=None)
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s, \
         patch("services.notifications.funnels.channel_bonus_reminder.is_channel_bonus_claimed", new=AsyncMock(return_value=False)), \
         patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        s.canall_url = "https://t.me/chan"
        s.channel_bonus_days = 7
        b.get_chat_member = AsyncMock(return_value=None)
        assert await funnel.should_send(make_ctx([k])) is False


@pytest.mark.asyncio
async def test_should_send_burst_guard_step1(funnel):
    """Due для шага 2, но last_sent < 1д назад → False (бёрст-гард), без live-проверки."""
    k = make_key(created_days_ago=10)
    funnel._state.get_state = AsyncMock(
        return_value={
            "step": 1,
            "last_sent_ms": int(datetime.now(timezone.utc).timestamp() * 1000) - 3_600_000,
            "key_email": "a@x.c",
        }
    )
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s, \
         patch("services.notifications.funnels.channel_bonus_reminder.is_channel_bonus_claimed", new=AsyncMock(return_value=False)), \
         patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        s.canall_url = "https://t.me/chan"
        s.channel_bonus_days = 7
        b.get_chat_member = AsyncMock(return_value={"status": "left"})
        assert await funnel.should_send(make_ctx([k])) is False
        b.get_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_should_send_terminal_step_skips(funnel):
    """step == MAX_STEPS (terminal) → False без live-проверки."""
    k = make_key(created_days_ago=10)
    from services.notifications.funnels.channel_bonus_reminder import MAX_STEPS as FMAX
    funnel._state.get_state = AsyncMock(return_value={"step": FMAX, "terminal": True})
    with patch("services.notifications.funnels.channel_bonus_reminder.settings") as s, \
         patch("services.notifications.funnels.channel_bonus_reminder.is_channel_bonus_claimed", new=AsyncMock(return_value=False)), \
         patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        s.canall_url = "https://t.me/chan"
        s.channel_bonus_days = 7
        b.get_chat_member = AsyncMock(return_value={"status": "left"})
        assert await funnel.should_send(make_ctx([k])) is False
        b.get_chat_member.assert_not_awaited()


# ---------- process ----------


@pytest.mark.asyncio
async def test_process_sent_advances_step(funnel):
    """send 'sent' со step 0 → advance_step вызван, sent=1, terminal НЕ вызван."""
    k = make_key(created_days_ago=10)
    funnel._state.get_state = AsyncMock(return_value=None)  # step 0
    funnel._state.advance_step = AsyncMock(return_value=1)
    with patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        b.send_message = AsyncMock(return_value=None)
        result = await funnel.process(make_ctx([k]))
    assert result.sent == 1
    funnel._state.advance_step.assert_awaited_once()
    funnel._state.mark_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_sent_last_step_marks_terminal(funnel):
    """send 'sent' со step 2 → advance_step возвращает 3 → mark_terminal."""
    k = make_key(created_days_ago=10)
    funnel._state.get_state = AsyncMock(return_value={"step": 2, "last_sent_ms": 1, "key_email": "a@x.c"})
    funnel._state.advance_step = AsyncMock(return_value=3)
    with patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        b.send_message = AsyncMock(return_value=None)
        result = await funnel.process(make_ctx([k]))
    assert result.sent == 1
    funnel._state.advance_step.assert_awaited_once()
    funnel._state.mark_terminal.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_blocked_no_state_change(funnel):
    """send 'blocked' → состояние не меняется, failed_blocked=1."""
    k = make_key(created_days_ago=10)
    funnel._state.get_state = AsyncMock(return_value=None)
    with patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        b.send_message = AsyncMock(side_effect=Exception("bot was blocked by the user"))
        result = await funnel.process(make_ctx([k]))
    assert result.failed_blocked == 1
    assert result.sent == 0
    funnel._state.advance_step.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_error_no_state_change(funnel):
    """send 'error' → состояние не меняется, failed_other=1."""
    k = make_key(created_days_ago=10)
    funnel._state.get_state = AsyncMock(return_value=None)
    with patch("services.notifications.funnels.channel_bonus_reminder.bot") as b:
        b.send_message = AsyncMock(side_effect=RuntimeError("timeout"))
        result = await funnel.process(make_ctx([k]))
    assert result.failed_other == 1
    funnel._state.advance_step.assert_not_awaited()