from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any


@dataclass
class Inbound:
    server_id: int
    inbound_id: int
    name_inbound: Optional[str] = None
    _name = "inbound"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Inbound":
        return cls(**data)

    @property
    def name(self):
        return self._name
