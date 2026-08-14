"""Ручной E2E-тест: создаём реального клиента в панели через XUISession.add_client
с subscription_link, проверяем что externalLinks реально прописались на панели.
Ключ создаётся с истечением через 5 минут — специально для быстрой самоочистки.
НЕ пишет ничего в БД платформы (не проходит через CreateKey.proces) — только панель.
"""
import asyncio
import json
import os
import sys
import time
import uuid

sys.path.insert(0, "/app")

from client import XUISession  # noqa: E402
from services.core.data.service import ServiceDataModel  # noqa: E402
from api.v1.mobile_mvp import _download_and_extract_vless  # noqa: E402


EXTERNAL_SUB_URL = os.environ["EXTERNAL_SUB_URL"]


class _StubServers:
    async def get_data(self, *_a, **_kw):
        return None  # форсируем fallback на get_env_server() из .env


async def main():
    model_data = ServiceDataModel.__new__(ServiceDataModel)
    model_data.servers = _StubServers()

    session = XUISession(model_service=model_data, loading=None)

    email = f"diag_ext_sub_{uuid.uuid4().hex[:8]}"
    client_id = str(uuid.uuid4())
    expiry_time = int((time.time() + 5 * 60) * 1000)  # +5 минут

    inbound_ids = json.loads(os.environ["AVAILABLE_CONNECTIONS"])

    print("=== email:", email)
    print("=== inbound_ids:", inbound_ids)
    print("=== expiry_time:", expiry_time, "(+5 минут от текущего момента)")
    print("=== external subscription url:", EXTERNAL_SUB_URL)

    result = await session.add_client(
        client_id=client_id,
        email=email,
        tg_id=0,
        limit_ip=1,
        inbound_ids=inbound_ids,
        expiry_time=expiry_time,
        subscription_link=EXTERNAL_SUB_URL,
    )
    print("=== add_client result:", result)
    assert result is True, "add_client вернул False — клиент не создан в панели"

    await session._ensure_standalone()
    raw = await session._standalone.get(email)
    print("=== panel get response:", json.dumps(raw, ensure_ascii=False, indent=2))

    sub_url = os.environ["XUI_SUB"].rstrip("/") + "/" + email
    print("=== subscription url (Key.key):", sub_url)

    vless = await asyncio.to_thread(_download_and_extract_vless, sub_url)
    print("=== vless config:", vless)


asyncio.run(main())
