from pydantic import BaseModel
from typing import List


class AdminGenerateKeyRequest(BaseModel):
    tg_id: int
    tariff_id: int
    server_id: int = 2
    number_of_months: int = 1


class AdminMassRenewRequest(BaseModel):
    emails: List[str]
    days: int = 30


class AdminChangeDateRequest(BaseModel):
    expiry_time: int


class AdminChangeTariffRequest(BaseModel):
    tariff_id: int
