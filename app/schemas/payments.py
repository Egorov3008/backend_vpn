from pydantic import BaseModel, model_validator
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


class PaymentCreateResponse(BaseModel):
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


class PaymentCalculateResponse(BaseModel):
    amount: float
    discount: float = 0.0
    final_amount: float
