from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any


def default_created_at():
    return int(datetime.now().timestamp() * 1000)


@dataclass
class Key:
    tg_id: int
    client_id: str
    email: str
    expiry_time: int
    key: str
    inbound_id: int
    inbound_ids: Optional[list] = None
    tariff_id: Optional[int] = None
    created_at: int = None
    reset_date: int = 0
    notified_10h: bool = False
    notified_24h: bool = False
    tariff_description: Optional[str] = None
    name_tariff: Optional[str] = None
    amount: Optional[float] = None
    limit_ip: Optional[int] = None
    period: Optional[int] = None
    used_traffic: Optional[float] = 0
    server_info: Optional[Any] = None
    notified_expired_grace: bool = False
    # Landing-page: anonymous 24h keys generated without user registration.
    # landing_uid — links key to the signed cookie on the landing page.
    # converted_tg_id — set by bot when user completes /start landing_<uid>.
    converted_tg_id: Optional[int] = None
    landing_uid: Optional[str] = None
    _name: str = "key"

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = default_created_at()

    _DB_FIELDS = frozenset(
        {
            "tg_id",
            "client_id",
            "email",
            "created_at",
            "expiry_time",
            "key",
            "reset_date",
            "inbound_id",
            "notified_10h",
            "notified_24h",
            "tariff_id",
            "limit_ip",
            "notified_expired_grace",
            "converted_tg_id",
            "landing_uid",
        }
    )

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if k in self._DB_FIELDS}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Key":
        return cls(**data)

    @property
    def name(self) -> str:
        return self._name

    @property
    def warp_expiry_time(self) -> str:
        """Возвращает время истечения ключа в формате 'ГГГГ-ММ-ДД ЧЧ:ММ'."""
        expiry_timestamp = self.expiry_time / 1000  # мс → сек
        dt = datetime.fromtimestamp(expiry_timestamp, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
