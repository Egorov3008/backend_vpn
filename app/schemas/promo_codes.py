from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from services.core.promo_codes.models import PromoCode, PromoUsage


class AdminPromoCodeCreateRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=32)
    discount_percent: int = Field(..., ge=1, le=100)
    starts_at: datetime
    ends_at: datetime
    max_redemptions: Optional[int] = Field(None, ge=1, description="Пусто — без лимита")
    audience: Literal["new", "all"] = Field("new", description="new — только до первой успешной оплаты")
    operations: Literal["create_key", "renew_key", "any"] = "any"
    max_months: Optional[int] = Field(1, ge=1, le=12, description="Скидка на первые N месяцев; пусто — на весь срок")


class AdminPromoCodeUpdateRequest(BaseModel):
    is_active: bool


class AdminPromoCodeResponse(BaseModel):
    id: int
    code: str
    discount_percent: int
    starts_at: datetime
    ends_at: datetime
    max_redemptions: Optional[int]
    audience: str
    operations: str
    max_months: Optional[int]
    is_active: bool
    created_at: Optional[datetime] = None
    used: int = 0
    used_new_users: int = 0
    reserved: int = 0

    @classmethod
    def build(cls, promo: PromoCode, usage: Optional[PromoUsage] = None) -> "AdminPromoCodeResponse":
        counts = (
            {"used": usage.used, "used_new_users": usage.used_new_users, "reserved": usage.reserved}
            if usage
            else {}
        )
        return cls(
            id=promo.id,
            code=promo.code,
            discount_percent=promo.discount_percent,
            starts_at=promo.starts_at,
            ends_at=promo.ends_at,
            max_redemptions=promo.max_redemptions,
            audience=promo.audience,
            operations=promo.operations,
            max_months=promo.max_months,
            is_active=promo.is_active,
            created_at=promo.created_at,
            **counts,
        )


class AdminPromoCodesListResponse(BaseModel):
    promo_codes: List[AdminPromoCodeResponse]


class AdminPromoCodeStatsResponse(BaseModel):
    promo_code: AdminPromoCodeResponse
    used: int
    used_new_users: int
    used_existing_users: int
    reserved_now: int
    abandoned: int
    revenue: float
    discount_total: float
    payments_before: int
    payments_during: int
    payments_after: int
    repeat_payers: int
    window_days: int


class WinbackCohortResponse(BaseModel):
    sent: int
    returned: int
    revenue: float


class AdminWinbackStatsResponse(BaseModel):
    enabled: bool
    discount_percent: int
    valid_hours: int
    min_days: int
    max_days: int
    control_percent: int
    days: int
    attribution_days: int
    offer: WinbackCohortResponse
    control: WinbackCohortResponse
    discount_total: float
