from pydantic import BaseModel
from typing import Optional


class TariffResponse(BaseModel):
    id: int
    name_tariff: str
    amount: float
    description: Optional[str] = None
    limit_ip: int = 0
    period: int = 30
    traffic_limit: int = 0

    @classmethod
    def from_tariff(cls, t) -> "TariffResponse":
        return cls(
            id=t.id,
            name_tariff=t.name_tariff,
            amount=t.amount,
            description=t.description,
            limit_ip=t.limit_ip,
            period=t.period,
            traffic_limit=t.traffic_limit,
        )
