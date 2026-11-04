from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Optional


@dataclass
class ApprovedInbound:
    """Inbound панели, утверждённый для новых ключей и массовой синхронизации.

    ``sort_order`` задаёт порядок набора (первый inbound исторически становится
    ``keys.inbound_id``), ``created_by_tg_id`` — кто одобрил (для аудита).
    """

    inbound_id: int
    sort_order: int = 0
    created_at: Optional[datetime] = field(default_factory=datetime.now)
    created_by_tg_id: Optional[int] = None
    _name: ClassVar[str] = "approved_inbounds"
