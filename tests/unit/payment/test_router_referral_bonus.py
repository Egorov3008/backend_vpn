"""
Тесты для PaymentRouter: начисление реферального бонуса от ПОЛНОЙ суммы платежа.

BUG- FIX: бонус рефереру должен считаться от полной суммы до скидки,
а не от суммы, которую заплатил пользователь со скидкой.

Пример:
- Тариф: 1000₽
- Реферальная скидка: 10% = 100₽
- Пользователь платит: 900₽
- Бонус рефереру: 100₽ (10% от 1000₽), а не 90₽ (10% от 900₽)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

from services.core.payment.router import PaymentRouter
from services.core.referral.bonus_service import ReferralBonusService


@pytest.fixture
def mock_processor():
    """Мок PaymentProcessor с полями amount и referral_discount."""
    processor = MagicMock()
    processor._conn = AsyncMock()
    processor._model_service = MagicMock()
    processor._model_service.users = MagicMock()
    processor.amount = 900.0  # Сумма ПОСЛЕ скидки
    processor.referral_discount = 100.0  # Скидка 10%
    processor.balance_discount = 0.0  # Без списания с баланса
    processor.tg_id = 200
    return processor


@pytest.fixture
def mock_services():
    """Моки для creation и renewal сервисов."""
    return {
        "creation_service": AsyncMock(),
        "renewal_service": AsyncMock(),
    }


class TestReferralBonusFullAmount:
    """Тесты для проверки расчёта бонуса от полной суммы."""

    async def test_bonus_calculated_from_full_amount_with_discount(
        self, mock_processor, mock_services, monkeypatch,
    ):
        """Бонус считается от ПОЛНОЙ суммы (amount + referral_discount)."""
        # Патчим bonus_service.process_referral_bonus для перехвата вызова
        call_args = {}

        async def mock_process(conn, referred_tg_id, payment_amount):
            call_args["payment_amount"] = payment_amount
            call_args["referred_tg_id"] = referred_tg_id

        bonus_service = MagicMock()
        bonus_service.process_referral_bonus = mock_process

        router = PaymentRouter(
            processor=mock_processor,
            creation_service=mock_services["creation_service"],
            renewal_service=mock_services["renewal_service"],
            bonus_service=bonus_service,
        )

        # Мокаем extract_operation и update_payment, чтобы не выполнять реальную обработку
        router.processor.extract_operation = MagicMock(return_value=["create_key", "1"])
        router.processor.update_payment = AsyncMock()
        router.processor.status = "pending"
        router.processor.load_payment_data = AsyncMock()

        # Выполняем route
        await router.route("pay_test_123")

        # Проверяем, что бонус рассчитан от ПОЛНОЙ суммы: 900 + 100 = 1000
        assert call_args.get("payment_amount") == 1000.0, (
            f"Бонус должен считаться от полной суммы 1000₽, "
            f"а не от {call_args.get('payment_amount')}₽"
        )
        assert call_args.get("referred_tg_id") == 200

    async def test_bonus_calculated_from_amount_when_no_discount(
        self, mock_processor, mock_services,
    ):
        """Когда referral_discount = 0, бонус считается от amount."""
        mock_processor.referral_discount = 0.0
        mock_processor.amount = 1000.0

        call_args = {}

        async def mock_process(conn, referred_tg_id, payment_amount):
            call_args["payment_amount"] = payment_amount

        bonus_service = MagicMock()
        bonus_service.process_referral_bonus = mock_process

        router = PaymentRouter(
            processor=mock_processor,
            creation_service=mock_services["creation_service"],
            renewal_service=mock_services["renewal_service"],
            bonus_service=bonus_service,
        )

        router.processor.extract_operation = MagicMock(return_value=["create_key", "1"])
        router.processor.update_payment = AsyncMock()
        router.processor.status = "pending"
        router.processor.load_payment_data = AsyncMock()

        await router.route("pay_test_456")

        # Когда скидки нет, бонус считается от amount = 1000
        assert call_args.get("payment_amount") == 1000.0

    async def test_bonus_not_called_when_no_bonus_service(
        self, mock_processor, mock_services,
    ):
        """Когда bonus_service = None, процесс не вызывается."""
        router = PaymentRouter(
            processor=mock_processor,
            creation_service=mock_services["creation_service"],
            renewal_service=mock_services["renewal_service"],
            bonus_service=None,  # Явно None
        )

        router.processor.extract_operation = MagicMock(return_value=["create_key", "1"])
        router.processor.update_payment = AsyncMock()
        router.processor.status = "pending"
        router.processor.load_payment_data = AsyncMock()

        # Не должно бросить исключение
        await router.route("pay_test_789")
