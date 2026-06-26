import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_claim_uses_upgrade_from_landing():
    from api.v1 import landing as L

    key_obj = MagicMock()
    key_obj.email = "a@b.c"
    key_obj.key = "https://sub.example/a@b.c"
    key_obj.expiry_time = 1000
    key_obj.converted_tg_id = None
    key_obj.tg_id = -1
    key_obj.inbound_ids = [7]
    key_obj.grace_expiry = None
    key_obj.client_id = "c1"
    key_obj.limit_ip = 1

    upgraded = MagicMock()
    upgraded.email = "a@b.c"
    upgraded.key = "https://sub.example/a@b.c"
    upgraded.expiry_time = 8000
    upgraded.grace_expiry = 8000 + 7 * 86_400_000

    pool = MagicMock()
    sd = MagicMock()
    sd.users.get_data = AsyncMock(return_value=MagicMock(tg_id=42, server_id=1))
    sd.cache_service.keys.all = AsyncMock(return_value=[key_obj])
    sd.keys.update = AsyncMock()
    sd.tariffs.get_data = AsyncMock(return_value=MagicMock(id=10, period=7, amount=0.0, name_tariff="trial", limit_ip=1))
    cache = MagicMock()
    cache.keys.set = AsyncMock()

    grace = MagicMock()
    grace.upgrade_from_landing = AsyncMock(return_value=upgraded)

    with patch.object(L, "_get_key_by_landing_uid", AsyncMock(return_value=key_obj)), \
         patch.object(L, "build_grace_manager", return_value=grace), \
         patch.object(L, "TrialService") as TS, \
         patch.object(L, "DataService", return_value=MagicMock()):
        TS.return_value.installation_trial = AsyncMock()
        # already_claimed guard: converted_tg_id None → proceeds
        resp = await L.claim_key(
            landing_uid="abc",
            body=L.ClaimRequest(tg_id=42),
            pool=pool,
            service_data=sd,
            cache=cache,
        )

    grace.upgrade_from_landing.assert_awaited_once()
    assert resp["status"] == "claimed"
    assert resp["email"] == "a@b.c"
