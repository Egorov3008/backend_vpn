from dataclasses import dataclass, asdict
from typing import Dict, Any, ClassVar


@dataclass
class Server:
    id: int
    cluster_name: str
    server_name: str
    api_url: str
    subscription_url: str
    login: str
    password: str
    _name: ClassVar[str] = "servers"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Server":
        return cls(**data)

    @property
    def name(self) -> str:
        return self._name
