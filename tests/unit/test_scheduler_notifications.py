import pytest
from unittest.mock import AsyncMock, MagicMock

from background.scheduler import SyncScheduler


@pytest.mark.asyncio
async def test_run_notifications_registers_channel_bonus_reminder():
    """run_notifications регистрирует ChannelBonusReminderFunnel в FunnelManager."""
    sd = MagicMock()
    pool = MagicMock()
    sched = SyncScheduler(service_data=sd, pool=pool)

    captured_funnel_ids: list[str] = []

    class FakeManager:
        def __init__(self, *a, **kw):
            pass

        def register(self, funnel):
            captured_funnel_ids.append(funnel.funnel_id)

        async def run_cycle(self):
            from services.notifications.models import FunnelRunReport
            return FunnelRunReport()

    # Локальный import в run_notifications читает этот атрибут модуля-источника.
    import services.notifications.manager as M
    M.FunnelManager = FakeManager

    try:
        await sched.run_notifications()
    finally:
        # Восстанавливаем настоящий класс, чтобы не влиять на другие тесты.
        from services.notifications.manager import FunnelManager as RealManager
        M.FunnelManager = RealManager

    assert "channel_bonus_reminder" in captured_funnel_ids