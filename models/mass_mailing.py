from dataclasses import dataclass, asdict
from typing import Dict, Any


@dataclass
class MassMailing:
    id: int
    title: str
    emoji: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MassMailing":
        return cls(**data)
