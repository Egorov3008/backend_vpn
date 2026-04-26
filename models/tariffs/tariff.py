from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, ClassVar


@dataclass
class Tariff:
    id: int
    name_tariff: str
    amount: float = 0.0
    description: Optional[str] = None
    limit_ip: int = 0
    period: int = 30
    traffic_limit: int = 0
    _name: ClassVar[str] = "tariff"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tariff":
        return cls(**data)

    @property
    def name(self) -> str:
        return self._name
