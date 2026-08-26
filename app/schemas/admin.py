from pydantic import BaseModel
from typing import Any, Dict, List, Optional

from app.schemas.users import UserResponse


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


class AdminMaintenanceModeRequest(BaseModel):
    enabled: bool
    reason: Optional[str] = None


class AdminUpdatePanelMetaRequest(BaseModel):
    group: Optional[str] = None
    comment: Optional[str] = None


# ── Response models ──────────────────────────────────────────────────────
# Отражают фактическую форму dict'ов, которые эндпоинты admin.py уже
# возвращали до этого патча (см. Этап 1 roadmap) — без изменения wire-формата,
# кроме отдельных документированных мест (см. комментарии).


class AdminStatsResponse(BaseModel):
    total_users: int
    total: int
    active: int
    trial: int
    expiring_24h: int
    expiring_7d: int
    expiring_30d: int
    unused: int
    expired: int
    other: int


class ChannelBonusStats(BaseModel):
    cumulative: int
    today: int
    yesterday: int


class AdminGraceBonusStatsResponse(BaseModel):
    channel_bonus: ChannelBonusStats


class AdminSchedulerStatusResponse(BaseModel):
    container_alive: bool
    users: int
    blocked: int
    keys: int
    segment_counts: Dict[str, int]


class MaintenanceStatusResponse(BaseModel):
    enabled: bool
    reason: Optional[str] = None
    enabled_at: Optional[str] = None
    enabled_by: Optional[int] = None


class AdminUserStockResponse(BaseModel):
    has_discount: bool
    stock_type: str
    value: float
    is_active: bool = False
    valid_until: Optional[str] = None


class AdminInactiveUsersResponse(BaseModel):
    count: int
    users: List[UserResponse]


class AdminDeleteCountResponse(BaseModel):
    deleted: int


class AdminGenerateKeyResponse(BaseModel):
    public_link: str
    days: int
    link_to_connect: str
    email: str


class AdminMassRenewResultItem(BaseModel):
    email: str
    success: bool
    new_expiry: Optional[int] = None
    error: Optional[str] = None


class AdminMassRenewResponse(BaseModel):
    total: int
    success: int
    failed: int
    results: List[AdminMassRenewResultItem]


class AdminChangeDateResponse(BaseModel):
    email: str
    expiry_time: int


class AdminChangeTariffResponse(BaseModel):
    email: str
    tariff_id: int


class AdminPanelMetaResponse(BaseModel):
    email: str
    group: Optional[str] = None
    comment: Optional[str] = None


class AdminGiftResponse(BaseModel):
    token: str
    sender_tg_id: int
    tariff_id: int
    created_at: Optional[str] = None
    # `used_at` — момент активации подарка; `redeemed_at` — то же значение под
    # именем, которое исторически ожидает часть клиентов (см. admin.py).
    redeemed_at: Optional[str] = None
    used_at: Optional[str] = None
    recipient_tg_id: Optional[int] = None
    recipient_email: Optional[str] = None


class AdminGiftsListResponse(BaseModel):
    gifts: List[AdminGiftResponse]


class AdminTariffDetailResponse(BaseModel):
    id: int
    name_tariff: str
    amount: float
    period: int
    traffic_limit: int


class AdminTariffItem(BaseModel):
    id: int
    name_tariff: str
    amount: float
    description: Optional[str] = None
    limit_ip: int = 0
    period: int = 30
    traffic_limit: int = 0

    @classmethod
    def from_tariff(cls, t) -> "AdminTariffItem":
        return cls(
            id=t.id,
            name_tariff=t.name_tariff,
            amount=t.amount,
            description=t.description,
            limit_ip=t.limit_ip,
            period=t.period,
            traffic_limit=t.traffic_limit,
        )


class AdminTariffsListResponse(BaseModel):
    tariffs: List[AdminTariffItem]


class AdminReferralLinkResponse(BaseModel):
    token: Optional[str] = None
    referrer_tg_id: int


class AdminReferralLinkDetailResponse(BaseModel):
    token: str
    referrer_tg_id: int
    created_at: Optional[str] = None
    id: Optional[int] = None


class AdminReferralStatsResponse(BaseModel):
    referral_count: int
    rewards_count: int
    rewards_total: float
    balance: float


class AdminKeyItem(BaseModel):
    """Полная форма Key (dataclass models/keys/key.py) — используется только
    для internal admin-эндпоинтов (не публичный контракт)."""

    tg_id: int
    client_id: str
    email: str
    expiry_time: int
    key: str
    inbound_id: int
    inbound_ids: Optional[List[int]] = None
    tariff_id: Optional[int] = None
    created_at: Optional[int] = None
    reset_date: int = 0
    notified_10h: bool = False
    notified_24h: bool = False
    tariff_description: Optional[str] = None
    name_tariff: Optional[str] = None
    amount: Optional[float] = None
    limit_ip: Optional[int] = None
    period: Optional[int] = None
    used_traffic: Optional[float] = 0
    notified_expired_grace: bool = False
    converted_tg_id: Optional[int] = None
    landing_uid: Optional[str] = None

    @classmethod
    def from_key(cls, k) -> "AdminKeyItem":
        return cls(**{f: getattr(k, f) for f in cls.model_fields if hasattr(k, f)})


class AdminKeysListResponse(BaseModel):
    keys: List[AdminKeyItem]


class AdminPaymentItem(BaseModel):
    """Полная форма PaymentModel (dataclass models/payments/payment.py)."""

    payment_id: str
    tg_id: Optional[int] = None
    amount: Optional[float] = None
    payment_type: Optional[str] = None
    status: str = "pending"
    number_of_months: int = 1
    discount_percent: int = 0
    referral_discount: float = 0.0
    balance_discount: float = 0.0
    created_at: Optional[Any] = None
    id: Optional[int] = None

    @classmethod
    def from_payment(cls, p) -> "AdminPaymentItem":
        return cls(**{f: getattr(p, f) for f in cls.model_fields if hasattr(p, f)})


class AdminPaymentsListResponse(BaseModel):
    payments: List[AdminPaymentItem]


class AdminKeyDeleteFailure(BaseModel):
    email: str
    error: str


class AdminDeleteUserResponse(BaseModel):
    deleted_user: bool
    keys_deleted: int
    keys_failed: List[AdminKeyDeleteFailure]


class AdminSyncStartedResponse(BaseModel):
    job_id: str
    status: str


class AdminSyncStatusResponse(BaseModel):
    status: str
    result: Optional[Any] = None
    error: Optional[str] = None
