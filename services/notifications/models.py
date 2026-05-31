"""Модели данных для системы уведомлений (backend scheduler)."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

from models.keys.key import Key
from models.users.user import User


@dataclass
class NotificationContext:
    """Контекст уведомления для одного пользователя в рамках воронки."""

    user: User
    keys: list[Key]
    segment_keys: list[Key]


@dataclass
class NotificationResult:
    """Результат обработки пользователя одной воронкой."""

    tg_id: int
    funnel_id: str
    sent: int = 0
    skipped: int = 0
    failed_blocked: int = 0
    failed_other: int = 0


@dataclass
class FunnelRunReport:
    """Отчёт о полном цикле уведомлений."""

    total_users: int = 0
    total_keys_segmented: int = 0
    results_by_funnel: Dict[str, Dict[str, int]] = field(default_factory=dict)
    duration_seconds: float = 0.0

    def add_result(self, result: NotificationResult) -> None:
        """Добавить результат воронки в отчёт."""
        fid = result.funnel_id
        if fid not in self.results_by_funnel:
            self.results_by_funnel[fid] = {
                "sent": 0,
                "skipped": 0,
                "failed_blocked": 0,
                "failed_other": 0,
            }
        stats = self.results_by_funnel[fid]
        stats["sent"] += result.sent
        stats["skipped"] += result.skipped
        stats["failed_blocked"] += result.failed_blocked
        stats["failed_other"] += result.failed_other
