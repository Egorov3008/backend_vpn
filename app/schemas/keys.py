from pydantic import BaseModel
from typing import Optional


class KeyResponse(BaseModel):
    email: str
    tg_id: int
    client_id: str
    expiry_time: int
    key: str
    tariff_id: Optional[int] = None
    name_tariff: Optional[str] = None
    total_gb: Optional[int] = None
    used_traffic: Optional[float] = None
    inbound_id: int
    public_link: Optional[str] = None
    link_to_connect: Optional[str] = None
    notified_10h: bool = False
    notified_24h: bool = False

    @classmethod
    def from_key(cls, k) -> "KeyResponse":
        # k.key уже содержит полный URL подписки (subscription_url/email)
        public_link = k.key
        link_to_connect = k.key
        return cls(
            email=k.email,
            tg_id=k.tg_id,
            client_id=k.client_id,
            expiry_time=k.expiry_time,
            key=k.key,
            tariff_id=k.tariff_id,
            name_tariff=k.name_tariff,
            total_gb=k.total_gb,
            used_traffic=k.used_traffic,
            inbound_id=k.inbound_id,
            public_link=public_link,
            link_to_connect=link_to_connect,
            notified_10h=getattr(k, "notified_10h", False),
            notified_24h=getattr(k, "notified_24h", False),
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
    tariff_id: int


class KeyRenewRequest(BaseModel):
    tg_id: int
    tariff_id: int
    number_of_months: int
