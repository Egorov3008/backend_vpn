from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from models import PaymentModel, Tariff
from models.stocks.stock import Stock
from api.v1.payments import _calculate_payment_amount, _apply_stock_to_tariff


@pytest.mark.asyncio
async def test_webhook_non_payment_event_returns_ok(api_client):
    response = await api_client.post("/api/v1/payments/webhook", json={
        "type": "notification",
        "event": "payment.waiting_for_capture",
        "object": {"id": "pay_123"},
    })
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_webhook_missing_payment_id_returns_400(api_client):
    response = await api_client.post("/api/v1/payments/webhook", json={
        "type": "notification",
        "event": "payment.succeeded",
        "object": {},
    })
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_create_payment_missing_tariff_returns_404(api_client, mock_service_data):
    mock_service_data.tariffs.get_data = AsyncMock(return_value=None)
    mock_service_data.users.get_data = AsyncMock(return_value=None)

    response = await api_client.post("/api/v1/payments/create", json={
        "tg_id": 123,
        "tariff_id": 999,
        "number_of_months": 1,
        "operation": "create_key",
    })
    assert response.status_code == 404


def _make_payments(count: int) -> list:
    return [
        PaymentModel(
            payment_id=f"pay_{i:03d}",
            tg_id=123,
            amount=10.0 * i,
            status="succeeded",
            payment_type="create_key|1",
            created_at=datetime(2026, 4, 27, 10, 0, 0),
        )
        for i in range(1, count + 1)
    ]


def test_calc_referred_first_purchase_applies_10_percent():
    """Сценарий из баг-репорта: приглашённый, первая оплата → 10% скидки."""
    result = _calculate_payment_amount(
        tariff_amount=160.0,
        number_of_months=1,
        referral_discount_from_request=None,
        user_referral_id=552810834,
        user_check_referral=False,
        user_tg_id=7563318767,
        user_balance=0.0,
    )
    assert result["referral_discount_amount"] == 16.0
    assert result["final_amount"] == 144.0
    assert result["has_referral_discount"] is True
    assert result["base_amount"] == 160.0


def test_calc_non_first_purchase_no_referral_discount():
    """check_referral=True → повторная оплата, 10% не начисляется."""
    result = _calculate_payment_amount(
        tariff_amount=160.0,
        number_of_months=1,
        referral_discount_from_request=None,
        user_referral_id=552810834,
        user_check_referral=True,
        user_tg_id=7563318767,
        user_balance=0.0,
    )
    assert result["referral_discount_amount"] == 0.0
    assert result["final_amount"] == 160.0
    assert result["has_referral_discount"] is False


def test_calc_self_referral_guard():
    """Защита от self-referral: referral_id == tg_id → нет скидки."""
    result = _calculate_payment_amount(
        tariff_amount=160.0,
        number_of_months=1,
        referral_discount_from_request=None,
        user_referral_id=7563318767,
        user_check_referral=False,
        user_tg_id=7563318767,
        user_balance=0.0,
    )
    assert result["referral_discount_amount"] == 0.0
    assert result["final_amount"] == 160.0


def test_calc_balance_capped_at_min_payment():
    """Списание баланса не может опустить платёж ниже MIN_PAYMENT_AMOUNT (10₽)."""
    result = _calculate_payment_amount(
        tariff_amount=160.0,
        number_of_months=1,
        referral_discount_from_request=None,
        user_referral_id=None,
        user_check_referral=False,
        user_tg_id=1,
        user_balance=200.0,
    )
    assert result["balance_discount_amount"] == 150.0
    assert result["final_amount"] == 10.0


def test_calc_stock_discount_passthrough():
    """stock_discount_amount передаётся сквозным полем; tariff_amount уже Stock-скидочный."""
    result = _calculate_payment_amount(
        tariff_amount=144.0,
        number_of_months=1,
        referral_discount_from_request=None,
        user_referral_id=None,
        user_check_referral=False,
        user_tg_id=1,
        user_balance=0.0,
        stock_discount_amount=16.0,
    )
    assert result["base_amount"] == 144.0
    assert result["stock_discount_amount"] == 16.0
    assert result["has_stock_discount"] is True
    assert result["final_amount"] == 144.0


# ---------------------------------------------------------------------------
# _apply_stock_to_tariff — Stock (fix/percent) application
# ---------------------------------------------------------------------------

def test_apply_stock_percent():
    stock = Stock(tg_id=1, stock_type="percent", value=10.0)
    price, per_month = _apply_stock_to_tariff(160.0, stock)
    assert price == 144.0
    assert per_month == 16.0


def test_apply_stock_fix():
    stock = Stock(tg_id=1, stock_type="fix", value=30.0)
    price, per_month = _apply_stock_to_tariff(160.0, stock)
    assert price == 130.0
    assert per_month == 30.0


def test_apply_stock_decimal_value_from_db():
    """stocks.value — DECIMAL: asyncpg отдаёт Decimal, цена тарифа — float."""
    stock = Stock(tg_id=1, stock_type="percent", value=Decimal("10.00"))
    price, per_month = _apply_stock_to_tariff(160.0, stock)
    assert price == 144.0
    assert per_month == 16.0


def test_apply_stock_inactive_returns_full_price():
    stock = Stock(tg_id=1, stock_type="percent", value=10.0, is_active=False)
    price, per_month = _apply_stock_to_tariff(160.0, stock)
    assert price == 160.0
    assert per_month == 0.0


def test_apply_stock_none_returns_full_price():
    price, per_month = _apply_stock_to_tariff(160.0, None)
    assert price == 160.0
    assert per_month == 0.0


# ---------------------------------------------------------------------------
# /payments/quotes endpoint — enriched response
# ---------------------------------------------------------------------------

def _pending_payment(payment_id: str) -> PaymentModel:
    return PaymentModel(
        payment_id=payment_id,
        tg_id=456,
        amount=99.99,
        status="pending",
        payment_type="create_key|1",
        created_at=datetime(2026, 10, 11, 10, 0, 0),
    )


@pytest.mark.asyncio
async def test_get_payment_status_yookassa_check_is_throttled(api_client, mock_service_data):
    """Повторные опросы того же платежа в пределах интервала не ходят в YooKassa."""
    mock_service_data.payments.get_data = AsyncMock(return_value=_pending_payment("pay_thr"))
    mock_service_data.payments.service.mark_canceled_if_pending = AsyncMock(return_value=False)

    with patch("yookassa.Payment.find_one", return_value=MagicMock(status="pending")) as mock_find:
        await api_client.get("/api/v1/payments/pay_thr/status?tg_id=456")
        await api_client.get("/api/v1/payments/pay_thr/status?tg_id=456")

    assert mock_find.call_count == 1


# ---------------------------------------------------------------------------
# /payments/create — второй счёт за ту же покупку не выставляется
# (баг: неудачная первая попытка, повторная покупка того же ключа → два ключа)
# ---------------------------------------------------------------------------

def _setup_repeat_purchase(mock_service_data, open_payments):
    tariff = Tariff(id=7, name_tariff="Premium", amount=160.0)
    mock_service_data.tariffs.get_data = AsyncMock(return_value=tariff)
    user = MagicMock(tg_id=123, referral_id=None, check_referral=False, balance=0.0)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.payments.service.list_open_for_user = AsyncMock(return_value=open_payments)
    mock_service_data.payments.service.mark_canceled_if_pending = AsyncMock(return_value=True)


def _open_payment(payment_id: str, *, amount: float = 160.0, months: int = 1, status: str = "pending"):
    return PaymentModel(
        payment_id=payment_id,
        tg_id=123,
        amount=amount,
        status=status,
        payment_type="create_key|7",
        number_of_months=months,
    )


def _yk_payment(status: str, url: str = "https://pay.example/old"):
    yk = MagicMock()
    yk.status = status
    yk.confirmation.confirmation_url = url
    return yk


_CREATE_BODY = {"tg_id": 123, "tariff_id": 7, "number_of_months": 1, "operation": "create_key"}
