"""Отчёт «Поведение» для веб-админки (GET /admin/behavior)."""

from services.behavior.period import BehaviorWindow, InvalidWindow
from services.behavior.service import BehaviorReportService

__all__ = ["BehaviorReportService", "BehaviorWindow", "InvalidWindow"]
