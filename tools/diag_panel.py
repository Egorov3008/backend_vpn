"""Read-only диагностика: какие inbound-ы реально есть в панели, и есть ли клиент dp5649."""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")

from client import _StandaloneClientAPI  # noqa: E402


async def main():
    base = os.environ["XUI_API_URL"]
    user = os.environ["XUI_LOGIN"]
    pwd = os.environ["XUI_PASSWORD"]
    token = os.environ.get("XUI_TOKEN") or os.environ.get("XUI_API_TOKEN")
    c = _StandaloneClientAPI(base_url=base, username=user, password=pwd, token=token)

    print("=== RUNTIME AVAILABLE_CONNECTIONS:", os.environ.get("AVAILABLE_CONNECTIONS"))
    print("=== XUI_API_URL:", base)

    # 1) inbounds list
    try:
        inb = await c.list_inbounds()
        objs = inb.get("obj", []) if isinstance(inb, dict) else inb
        ids = [o.get("id") for o in (objs or [])]
        print("=== PANEL INBOUND IDs:", ids)
        print("=== full inbounds (truncated):", json.dumps(inb, ensure_ascii=False)[:1500])
    except Exception as e:
        print("=== INBOUND ERR:", repr(e))

    # 2) client dp5649
    try:
        cl = await c.get("dp5649")
        print("=== CLIENT dp5649 resp:", json.dumps(cl, ensure_ascii=False)[:1500])
    except Exception as e:
        print("=== CLIENT dp5649 ERR:", repr(e))

    # 3) list clients (truncated) to confirm dp5649 absence
    try:
        lst = await c.list_clients()
        emails = [x.get("email") for x in (lst.get("obj", []) if isinstance(lst, dict) else lst)]
        print("=== panel client emails (truncated):", emails[:50])
        print("=== dp5649 in panel clients?", "dp5649" in emails)
    except Exception as e:
        print("=== LIST CLIENTS ERR:", repr(e))


asyncio.run(main())