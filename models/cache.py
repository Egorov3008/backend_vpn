from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class CacheItem:
    value: Any
    expires_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CacheItem":
        return cls(**data)


@dataclass
class REGISTRATE_USER:
    tg_id: int
    is_msg: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "REGISTRATE_USER":
        return cls(**data)
