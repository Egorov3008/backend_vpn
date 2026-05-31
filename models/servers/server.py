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


def get_env_server() -> Server:
    """Build a Server object from environment (.env) settings.

    Used as a fallback when the ``servers`` database table is empty
    (legacy multi-server schema, now single-panel mode).
    """
    from config import settings

    return Server(
        id=settings.xui_server_id,
        cluster_name="default",
        server_name="main",
        api_url=settings.api_url,
        subscription_url=settings.xui_subscription_url or settings.api_url,
        login=settings.admin_username,
        password=settings.admin_password,
    )
