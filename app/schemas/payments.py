from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal
from datetime import datetime


class PaymentWebhookBody(BaseModel):
    type: str
    event: str
    object: dict


class PaymentCreateRequest(BaseModel):
    tg_id: int
    tariff_id: int
    number_of_months: int = 1
    operation: Literal["create_key", "renew_key"] = "create_key"
    email: Optional[str] = None
    customer_email: Optional[str] = None  # For fiscal receipt
    amount: Optional[float] = None  # Override tariff price (e.g. after discounts)
    referral_discount: Optional[float] = None  # Two-sided referral discount passed from bot
    promo_code: Optional[str] = Field(None, max_length=64)


class PromoFieldsMixin(BaseModel):
    """Что стало с промокодом из запроса; все поля пустые, если кода не было.

    promo_status: applied — скидка по коду в сумме; not_better — у пользователя уже
    есть скидка больше, код не применён; иначе причина отказа (not_found, expired,
    limit_reached, ...). promo_message — готовый текст для пользователя.
    """
    promo_code: Optional[str] = None
    promo_status: Optional[str] = None
    promo_message: Optional[str] = None
    promo_discount_amount: float = 0.0


class PaymentCreateResponse(PromoFieldsMixin):
    payment_id: str
    confirmation_url: str
    amount: float


class PaymentHistoryItem(BaseModel):
    payment_id: str
    tg_id: int
    amount: float
    status: str
    payment_type: Optional[str] = None
    created_at: Optional[datetime] = None


class PaymentStatusResponse(BaseModel):
    payment_id: str
    status: str
    tg_id: int
    processed: bool = False

    @model_validator(mode='after')
    def set_processed(self):
        self.processed = self.status == "succeeded"
        return self


class PaymentCalculateRequest(BaseModel):
    tg_id: int
    tariff_id: int
    number_of_months: int = 1
    operation: Literal["create_key", "renew_key"] = "create_key"
    promo_code: Optional[str] = Field(None, max_length=64)


class PaymentCalculateResponse(PromoFieldsMixin):
    final_amount: float
    # Полная разбивка скидок — бот рендерит окно оплаты по этим полям
    # (источник истины — backend, см. _calculate_payment_amount).
    base_amount: float = 0.0
    stock_discount_amount: float = 0.0
    volume_discount_percent: int = 0
    volume_discount_amount: float = 0.0
    referral_discount_amount: float = 0.0
    balance_discount_amount: float = 0.0
    has_stock_discount: bool = False
    has_promo_discount: bool = False
    has_volume_discount: bool = False
    has_referral_discount: bool = False
    has_balance_discount: bool = False
