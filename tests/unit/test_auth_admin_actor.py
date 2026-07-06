import pytest
from unittest.mock import patch
from app.auth import verify_admin_actor, AdminPrincipal


@pytest.mark.asyncio
async def test_no_key_returns_401():
    with patch("app.auth.settings") as s:
        s.admin_api_key = "secret"
        with pytest.raises(Exception) as exc:
            await verify_admin_actor(x_api_key=None, x_bot_secret=None, x_admin_tg_id=None)
        assert "401" in str(exc.value) or "Invalid" in str(exc.value)


@pytest.mark.asyncio
async def test_bot_secret_not_accepted():
    with patch("app.auth.settings") as s:
        s.admin_api_key = "secret"
        with pytest.raises(Exception):
            await verify_admin_actor(x_api_key=None, x_bot_secret="bot-secret", x_admin_tg_id=None)


@pytest.mark.asyncio
async def test_valid_key_returns_principal():
    with patch("app.auth.settings") as s:
        s.admin_api_key = "secret"
        principal = await verify_admin_actor(x_api_key="secret", x_bot_secret=None, x_admin_tg_id="999")
        assert isinstance(principal, AdminPrincipal)
        assert principal.admin_tg_id == 999


@pytest.mark.asyncio
async def test_invalid_admin_tg_id_falls_back_to_none():
    with patch("app.auth.settings") as s:
        s.admin_api_key = "secret"
        principal = await verify_admin_actor(x_api_key="secret", x_bot_secret=None, x_admin_tg_id="not-int")
        assert principal.admin_tg_id is None