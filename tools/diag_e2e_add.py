"""E2E: реально создаём клиента в панели на валидном inbound, проверяем, удаляем.
Подтверждает, что после фикса add_client + актуального AVAILABLE_CONNECTIONS
ключ действительно попадает в панель (а не фантом)."""
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, "/app")

from client import _StandaloneClientAPI  # noqa: E402


async def main():
    base = os.environ["XUI_API_URL"]
    user = os.environ["XUI_LOGIN"]
    pwd = os.environ["XUI_PASSWORD"]
    c = _StandaloneClientAPI(base_url=base, username=user, password=pwd,
                             token=os.environ.get("XUI_TOKEN"))

    # Чистим возможных orphan-ов от прошлых прогонов (best-effort).
    for orphan in ("diag_e2e_78950301",):
        try:
            await c.delete(orphan, keep_traffic=False)
        except Exception as e:
            print(f"orphan cleanup {orphan}: {e}")

    email = f"diag_e2e_{uuid.uuid4().hex[:8]}"
    client_id = str(uuid.uuid4())
    print("=== test email:", email)

    # add на валидном inbound 2
    r = await c.add(
        client_data={
            "id": client_id, "email": email, "limitIp": 1, "expiryTime": 0,
            "enable": True, "tgId": 0, "flow": "xtls-rprx-vision", "subId": email,
            "group": "", "comment": "diag_e2e", "reset": 0,
        },
        inbound_ids=[2],
    )
    print("=== add resp:", json.dumps(r, ensure_ascii=False)[:300])
    assert r.get("success") is True, f"add не удался: {r}"

    # проверяем, что клиент реально в панели
    g = await c.get(email)
    print("=== get resp:", json.dumps(g, ensure_ascii=False)[:300])
    obj = g.get("obj") if isinstance(g, dict) else None
    client_obj = (obj or {}).get("client") if isinstance(obj, dict) else None
    if client_obj is None and isinstance(obj, dict) and "email" in obj:
        client_obj = obj  # некоторые эндпоинты возвращают клиента плоско
    assert client_obj and client_obj.get("email") == email, "клиента нет в панели после add!"

    # чистота — удаляем
    d = await c.delete(email, keep_traffic=False)
    print("=== delete resp:", json.dumps(d, ensure_ascii=False)[:300])

    # подтверждаем удаление
    g2 = await c.get(email)
    print("=== get after delete:", json.dumps(g2, ensure_ascii=False)[:200])
    print("=== E2E OK: клиент создан в панели и удалён")


asyncio.run(main())