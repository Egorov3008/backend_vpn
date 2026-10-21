import pytest
from unittest.mock import AsyncMock, MagicMock

from services.core.keys.utils.renewal import KeyRenewal
from services.system.maintenance import PanelMaintenanceError, maintenance_mode


@pytest.fixture(autouse=True)
def _maintenance_mode_off(monkeypatch):
    """conn в этих тестах — MagicMock, не asyncpg.Pool; отключаем реальную БД-проверку."""
    monkeypatch.setattr(maintenance_mode, "is_enabled", AsyncMock(return_value=False))


def _key(expiry=2000, tariff_id=5, email="a@b.c"):
    k = MagicMock()
    k.expiry_time = expiry
    k.tariff_id = tariff_id
    k.email = email
    k.client_id = "c1"
    k.tg_id = 1
    k.limit_ip = 3
    k.server_info = None
    k.created_at = 0
    return k


def _renewal():
    xui = MagicMock()
    xui.extend_client_key = AsyncMock(return_value=True)
    xui.set_inbounds = AsyncMock(return_value=True)
    md = MagicMock()
    md.keys.update = AsyncMock()
    refresh = MagicMock()
    refresh.refresh_key = MagicMock(side_effect=lambda k, *a, **kw: k)
    resetter = MagicMock()
    resetter.reset_key_after_renewal = AsyncMock()
    kr = KeyRenewal(model_data=md, xui_session=xui, refresh_key=refresh, resetter=resetter)
    return kr, xui, md, refresh, resetter


@pytest.mark.asyncio
async def test_extension_blocked_when_maintenance_mode_enabled(monkeypatch):
    """Режим профилактики включён → PanelMaintenanceError до обращения к панели."""
    monkeypatch.setattr(maintenance_mode, "is_enabled", AsyncMock(return_value=True))
    kr, xui, md, refresh, resetter = _renewal()
    k = _key(expiry=10**13)
    md.keys.get_data = AsyncMock(return_value=k)
    tariff = MagicMock(id=5, period=30, amount=100.0, name_tariff="m", limit_ip=3)

    with pytest.raises(PanelMaintenanceError):
        await kr.extension_key(k, conn=MagicMock(), server=MagicMock(), tariff=tariff, number_of_months=1)

    xui.extend_client_key.assert_not_awaited()
    xui.set_inbounds.assert_not_awaited()
    md.keys.update.assert_not_awaited()
