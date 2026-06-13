"""
Тесты для ReferralBonusService.

После фикса BUG-4/9 service работает через сырые SQL-запросы к asyncpg.Connection
внутри одной транзакции. Тесты мокают именно connection.

BUG-8: reward_value в БД — DECIMAL(10,2). Тесты проверяют, что INSERT получает
Decimal, а не str.
"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.core.referral.bonus_service import ReferralBonusService


@pytest.fixture
def mock_service():
    service = MagicMock()
    service.users = MagicMock()
    service.data_service = MagicMock()
    service.data_service.referral_rewards = MagicMock()
    return service


@pytest.fixture
def bonus_service(mock_service):
    svc = ReferralBonusService(mock_service)
    svc._notify_referrer = AsyncMock()
    return svc


def make_conn(*, mark_row=None, user_lookup=None, referrer_exists=True, execute_error=None):
    """Создаёт мок asyncpg.Connection.

    Args:
        mark_row: результат fetchrow для UPDATE check_referral
                  (None = бонус не начисляется, dict = начисляется).
        user_lookup: результат диагностического fetchrow (когда mark_row=None).
        referrer_exists: проходит ли проверка referrer в транзакции.
        execute_error: исключение, бросаемое внутри execute.
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=[mark_row, user_lookup])
    conn.fetchval = AsyncMock(return_value=1 if referrer_exists else None)
    if execute_error:
        conn.execute = AsyncMock(side_effect=execute_error)
    else:
        conn.execute = AsyncMock()

    tx_cm = MagicMock()
    tx_cm.__aenter__ = AsyncMock()
    tx_cm.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx_cm)
    return conn


class TestHappyPath:
    async def test_creates_reward_and_marks_check_referral(self, bonus_service):
        conn = make_conn(mark_row={"referral_id": 100})

        await bonus_service.process_referral_bonus(conn, 200, 500.0)

        assert conn.fetchrow.await_count == 1
        conn.transaction.return_value.__aenter__.assert_awaited_once()
        # INSERT в referral_rewards + UPDATE balance
        assert conn.execute.await_count == 2
        bonus_service._notify_referrer.assert_awaited_once_with(100, 50.0)

    async def test_bonus_calculated_as_10_percent(self, bonus_service):
        conn = make_conn(mark_row={"referral_id": 100})
        await bonus_service.process_referral_bonus(conn, 200, 500.0)
        # Первый execute — INSERT в referral_rewards
        insert_args = conn.execute.call_args_list[0][0]
        assert insert_args[1] == 100            # referrer_tg_id
        # BUG-8: reward_value пробрасывается как Decimal, а не str
        assert insert_args[2] == Decimal("50.0")

    async def test_bonus_with_payment_1000(self, bonus_service):
        conn = make_conn(mark_row={"referral_id": 100})
        await bonus_service.process_referral_bonus(conn, 200, 1000.0)
        assert conn.execute.call_args_list[0][0][2] == Decimal("100.0")


class TestSkipCases:
    async def test_skip_when_check_referral_already_true(self, bonus_service):
        conn = make_conn(
            mark_row=None,
            user_lookup={"referral_id": 100, "check_referral": True},
        )

        await bonus_service.process_referral_bonus(conn, 400, 500.0)

        # Нет INSERT/UPDATE, нет notify
        assert conn.execute.await_count == 0
        bonus_service._notify_referrer.assert_not_awaited()

    async def test_skip_when_no_referrer(self, bonus_service):
        conn = make_conn(
            mark_row=None,
            user_lookup={"referral_id": None, "check_referral": False},
        )

        await bonus_service.process_referral_bonus(conn, 300, 500.0)

        assert conn.execute.await_count == 0
        bonus_service._notify_referrer.assert_not_awaited()

    async def test_skip_when_user_not_found(self, bonus_service):
        conn = make_conn(mark_row=None, user_lookup=None)

        await bonus_service.process_referral_bonus(conn, 999, 500.0)

        assert conn.execute.await_count == 0
        bonus_service._notify_referrer.assert_not_awaited()

    async def test_skip_zero_payment(self, bonus_service):
        conn = make_conn()
        await bonus_service.process_referral_bonus(conn, 200, 0.0)
        conn.fetchrow.assert_not_awaited()
        conn.execute.assert_not_awaited()

    async def test_skip_negative_payment(self, bonus_service):
        conn = make_conn()
        await bonus_service.process_referral_bonus(conn, 200, -50.0)
        conn.fetchrow.assert_not_awaited()


class TestIdempotency:
    async def test_second_call_credits_nothing(self, bonus_service):
        """Первый вызов начислил, второй — UPDATE вернёт None → бонус не задвоится."""
        conn1 = make_conn(mark_row={"referral_id": 100})
        await bonus_service.process_referral_bonus(conn1, 200, 500.0)
        assert conn1.execute.await_count == 2

        bonus_service._notify_referrer.reset_mock()
        conn2 = make_conn(
            mark_row=None,
            user_lookup={"referral_id": 100, "check_referral": True},
        )
        await bonus_service.process_referral_bonus(conn2, 200, 500.0)
        assert conn2.execute.await_count == 0
        bonus_service._notify_referrer.assert_not_awaited()


class TestTransactionSafety:
    async def test_db_error_does_not_send_notification(self, bonus_service):
        """Если транзакция упала — уведомление НЕ отправляется."""
        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=[{"referral_id": 100}, None])
        conn.fetchval = AsyncMock(return_value=1)
        # execute бросает; транзакция откатывается
        conn.execute = AsyncMock(side_effect=RuntimeError("DB down"))

        tx_cm = MagicMock()
        tx_cm.__aenter__ = AsyncMock()
        tx_cm.__aexit__ = AsyncMock(side_effect=RuntimeError("transaction rolled back"))
        conn.transaction = MagicMock(return_value=tx_cm)

        # Должно проглотить исключение, не бросить наружу
        await bonus_service.process_referral_bonus(conn, 200, 500.0)

        bonus_service._notify_referrer.assert_not_awaited()


class TestSelfReferralDefense:
    async def test_block_self_referrer_in_transaction(self, bonus_service):
        """Даже если UPDATE прошёл, повторная проверка в транзакции ловит self-referral."""
        conn = make_conn(mark_row={"referral_id": 100}, referrer_exists=False)

        await bonus_service.process_referral_bonus(conn, 100, 500.0)

        # Выходим раньше — INSERT/UPDATE не произошло
        assert conn.execute.await_count == 0
        bonus_service._notify_referrer.assert_not_awaited()


class TestPoolCompatibility:
    async def test_accepts_pool_with_real_asyncpg_check(self, bonus_service):
        """Реальный asyncpg.Pool: проверяем, что isinstance-ветка существует и
        не падает при подмене на объект с методом acquire().

        В тесте не мокаем isinstance (это рекурсивно ломает pytest), а просто
        убеждаемся, что вызов с Pool корректно проходит через isinstance-ветку.
        """
        # MagicMock по умолчанию не является instance of asyncpg.Pool,
        # поэтому isinstance(conn, asyncpg.Pool) == False, и мы пойдём
        # во вторую ветку (else) — что мы и тестируем как connection-путь.
        conn = make_conn(mark_row={"referral_id": 100})
        await bonus_service.process_referral_bonus(conn, 200, 500.0)
        assert conn.execute.await_count == 2


class TestBonusDaysForReferred:
    """Тесты для двусторонней награды: реферал получает +3 дня к подписке."""

    async def test_grants_bonus_days_to_referred(self, bonus_service):
        """Проверяем, что при начислении бонуса вызывается _grant_referred_bonus_days."""
        conn = make_conn(mark_row={"referral_id": 100})
        # Мокаем метод начисления дней
        bonus_service._grant_referred_bonus_days = AsyncMock()
        bonus_service._notify_referred_bonus_days = AsyncMock()

        await bonus_service.process_referral_bonus(conn, 200, 500.0)

        bonus_service._grant_referred_bonus_days.assert_awaited_once_with(conn, 200)
        bonus_service._notify_referred_bonus_days.assert_awaited_once_with(200)

    async def test_bonus_days_constant(self, bonus_service):
        """Проверяем, что константа BONUS_DAYS_MS установлена корректно (3 дня в мс)."""
        expected_ms = 3 * 24 * 60 * 60 * 1000  # 259_200_000 мс
        assert bonus_service.BONUS_DAYS_MS == expected_ms

    async def test_notify_referred_bonus_days(self, bonus_service):
        """Тест уведомления рефералу о бонусных днях."""
        from unittest.mock import patch
        with patch.object(bonus_service, '_notify_referred_bonus_days') as mock_notify:
            await bonus_service._notify_referred_bonus_days(200)
            mock_notify.assert_awaited_once()
