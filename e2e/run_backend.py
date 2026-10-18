"""Запуск backend для e2e: внешние сервисы перенаправлены на e2e/fakes.py.

Код backend не меняется. До импорта приложения:

- httpx-запросы на ``api.telegram.org`` уходят в фейковый Bot API;
- SDK ЮKassa смотрит в фейковый ``/v3``.

Адрес заглушек — ``E2E_FAKES_URL`` (например ``http://127.0.0.1:9100``).
"""
import os
import sys

import httpx

FAKES = httpx.URL(os.environ["E2E_FAKES_URL"])
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

_orig_send = httpx.AsyncClient.send


async def _send(self, request, **kwargs):
    if request.url.host == "api.telegram.org":
        request.url = request.url.copy_with(scheme=FAKES.scheme, host=FAKES.host, port=FAKES.port)
        request.headers["host"] = f"{FAKES.host}:{FAKES.port}"
    return await _orig_send(self, request, **kwargs)


httpx.AsyncClient.send = _send

import yookassa  # noqa: E402

yookassa.Configuration.api_url = f"{str(FAKES).rstrip('/')}/v3"

import uvicorn  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("E2E_BACKEND_PORT", "8000"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")
