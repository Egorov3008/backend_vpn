from pydantic import BaseModel
from typing import Optional


class KeyResponse(BaseModel):
    email: str
    tg_id: int
    expiry_time: int
    key: str
    tariff_id: Optional[int] = None
    name_tariff: Optional[str] = None
    total_gb: Optional[int] = None
    used_traffic: Optional[float] = None
    inbound_id: int

    @classmethod
    def from_key(cls, k) -> "KeyResponse":
        return cls(
            email=k.email,
            tg_id=k.tg_id,
            expiry_time=k.expiry_time,
            key=k.key,
            tariff_id=k.tariff_id,
            name_tariff=k.name_tariff,
            total_gb=k.total_gb,
            used_traffic=k.used_traffic,
            inbound_id=k.inbound_id,
        )


class KeyDetailResponse(KeyResponse):
    status_text: str
    days_left: int
    hours_left: int
    is_active: bool
    is_trial: bool
    expiry_date: str


class KeyCreateRequest(BaseModel):
    tg_id: int
