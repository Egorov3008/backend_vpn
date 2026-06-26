import pytest
from unittest.mock import AsyncMock, MagicMock
from client import XUISession


def _xui_with_panel(inbound_ids, attach_ok=True, detach_ok=True):
    xui = XUISession.__new__(XUISession)
    xui._standalone = MagicMock()
    raw = {"obj": {"client": {"inboundIds": inbound_ids}}}
    xui._standalone.get = AsyncMock(return_value=raw)
    xui._standalone.attach = AsyncMock(return_value={"success": attach_ok})
    xui._standalone.detach = AsyncMock(return_value={"success": detach_ok})
    xui.ensure_auth = AsyncMock()
    xui._ensure_standalone = AsyncMock()
    return xui


@pytest.mark.asyncio
async def test_noop_when_already_correct():
    xui = _xui_with_panel([7, 11, 12])
    ok = await xui.set_inbounds("a@b.c", [7, 11, 12])
    assert ok is True
    xui._standalone.attach.assert_not_called()
    xui._standalone.detach.assert_not_called()


@pytest.mark.asyncio
async def test_attaches_missing_and_detaches_extra():
    xui = _xui_with_panel([7, 99])  # has 99 (extra), missing 11,12
    ok = await xui.set_inbounds("a@b.c", [7, 11, 12])
    assert ok is True
    # implementation calls _standalone.attach(email, [i]) positionally:
    # call_args.args == (email, [i]) → the inbound id is args[1][0]
    attached = {c.args[1][0] for c in xui._standalone.attach.call_args_list}
    detached = {c.args[1][0] for c in xui._standalone.detach.call_args_list}
    assert attached == {11, 12}
    assert detached == {99}


@pytest.mark.asyncio
async def test_returns_false_when_client_missing():
    xui = _xui_with_panel([7])
    xui._standalone.get = AsyncMock(side_effect=Exception("not found"))
    ok = await xui.set_inbounds("a@b.c", [7, 11, 12])
    assert ok is False
