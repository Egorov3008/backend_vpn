from pydantic import BaseModel
from typing import Optional, Literal


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


class PaymentCreateResponse(BaseModel):
    payment_id: str
    confirmation_url: str
    amount: float
