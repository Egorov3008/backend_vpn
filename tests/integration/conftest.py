"""Стенд интеграционных тестов: живой Postgres + заглушки внешних сервисов.

Нужна переменная TEST_DATABASE_URL (любая база, к которой есть права на CREATE SCHEMA).
Без неё все тесты этой папки пропускаются. Пример для локального Postgres:

    TEST_DATABASE_URL=postgresql://test@127.0.0.1:5432/test pytest tests/integration

На сессию создаётся своя схема ``test_<hex>``: db/schema_fixed.sql + все миграции из
db/migrations/. Тесты пишут только в неё, в конце сессии схема удаляется. Перед каждым
тестом таблицы очищаются (TRUNCATE).

Внешние сервисы — те же заглушки, что и в e2e (e2e/fakes.py), поднятые в потоке:
3x-UI (/panel/api), ЮKassa (/v3/payments, SDK смотрит туда), Telegram Bot API
(httpx-запросы на api.telegram.org перенаправляются туда же).
"""
import asyncio
import importlib.util
import os
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import asyncpg
import httpx
import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[2]
INTEGRATION_DIR = Path(__file__).resolve().parent

# Конфигурация стенда (совпадает с e2e/stack.py)
LANDING_INBOUND = 5
PAID_INBOUNDS = [11, 12]
TRIAL_TARIFF_ID = 10
STANDARD_TARIFF_ID = 7
FAMILY_TARIFF_ID = 8
PAID_TARIFF_IDS = [STANDARD_TARIFF_ID, FAMILY_TARIFF_ID]
TARIFFS = [
    (TRIAL_TARIFF_ID, "Пробный", 0.0, "Бесплатный пробный период", 1, 7),
    (STANDARD_TARIFF_ID, "Стандарт", 199.0, "1 устройство", 1, 30),
    (FAMILY_TARIFF_ID, "Семейный", 349.0, "До 3 устройств", 3, 30),
]
BOT_TOKEN = "100500:integration-test-token"
LANDING_INTERNAL_SECRET = "integration-landing-internal-secret"
BOT_ME = {"id": 100500, "is_bot": True, "first_name": "Test", "username": "integration_test_bot"}


def pytest_collection_modifyitems(config, items):
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL не задана — нужен живой Postgres (см. tests/integration/conftest.py)")
    for item in items:
        if INTEGRATION_DIR in Path(str(item.fspath)).resolve().parents:
            item.add_marker(skip)


# --- база -------------------------------------------------------------------


def _schema_sql() -> list[str]:
    files = [ROOT / "db" / "schema_fixed.sql", *sorted((ROOT / "db" / "migrations").glob("*.sql"))]
    return [f.read_text(encoding="utf-8") for f in files]


async def _create_schema(name: str) -> None:
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(f'CREATE SCHEMA "{name}"')
        await conn.execute(f'SET search_path TO "{name}"')
        for sql in _schema_sql():
            await conn.execute(sql)
    finally:
        await conn.close()


async def _drop_schema(name: str) -> None:
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def db_schema():
    """Изолированная схема на всю сессию; рабочие схемы базы не трогаются."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_create_schema(name))
    yield name
    asyncio.run(_drop_schema(name))


@pytest.fixture
async def pool(db_schema):
    """Пул на тестовую схему; таблицы пустые в начале каждого теста."""
    p = await asyncpg.create_pool(
        TEST_DATABASE_URL, min_size=1, max_size=20, server_settings={"search_path": db_schema}
    )
    tables = await p.fetch("SELECT tablename FROM pg_tables WHERE schemaname = $1", db_schema)
    if tables:
        names = ", ".join(f'"{db_schema}"."{t["tablename"]}"' for t in tables)
        await p.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    yield p
    await p.close()


# --- заглушки внешних сервисов ----------------------------------------------


def _load_fakes_module():
    spec = importlib.util.spec_from_file_location("e2e_fakes", ROOT / "e2e" / "fakes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Fakes:
    """Заглушки 3x-UI, ЮKassa и Telegram (e2e/fakes.py) в отдельном потоке."""

    url: str
    _http: httpx.Client = field(repr=False)

    def reset(self) -> None:
        self._http.post(
            "/_ctl/reset",
            json={"bots": {BOT_TOKEN: BOT_ME}, "inbounds": [LANDING_INBOUND, *PAID_INBOUNDS]},
        ).raise_for_status()

    def state(self) -> dict:
        return self._http.get("/_ctl/state").json()

    def panel_client(self, email: str) -> Optional[dict]:
        """Клиент панели {"client": {...}, "inboundIds": [...]} или None."""
        return self.state()["clients"].get(email)

    def sent_messages(self, chat_id: int) -> list[dict]:
        return [m for m in self.state()["sent"] if m["chat_id"] == chat_id]

    def set_payment_status(self, payment_id: str, status: str) -> dict:
        resp = self._http.post(f"/_ctl/yk/{payment_id}/status", json={"status": status})
        resp.raise_for_status()
        return resp.json()

    def add_panel_client(self, email: str, client_id: str, expiry_time: int, inbound_ids: list[int]) -> None:
        resp = self._http.post(
            "/panel/api/clients/add",
            json={"client": {"id": client_id, "email": email, "expiryTime": expiry_time, "enable": True},
                  "inboundIds": inbound_ids},
        )
        assert resp.json()["success"], resp.text

    def fail_panel(self, *paths: str) -> None:
        self._http.post("/_ctl/panel/fail", json={"paths": list(paths)}).raise_for_status()

    def set_chat_member(self, user_id: int, status: str) -> None:
        self._http.post("/_ctl/tg/chat-member", json={"chat_id": "*", "user_id": user_id, "status": status}).raise_for_status()


@pytest.fixture(scope="session")
def _fakes_server():
    import uvicorn

    module = _load_fakes_module()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(module.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("заглушки не запустились")
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    with httpx.Client(base_url=url, timeout=10, trust_env=False) as http:
        yield Fakes(url=url, _http=http)
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def fakes(_fakes_server) -> Fakes:
    _fakes_server.reset()
    return _fakes_server


def _redirect_telegram(monkeypatch, fakes_url: str) -> None:
    """httpx-запросы на api.telegram.org уходят в заглушку под токеном стенда."""
    target = httpx.URL(fakes_url)
    original = httpx.AsyncClient.send

    async def send(self, request, **kwargs):
        if request.url.host == "api.telegram.org":
            path = re.sub(r"^/bot[^/]*/", f"/bot{BOT_TOKEN}/", request.url.path)
            request.url = request.url.copy_with(
                scheme=target.scheme, host=target.host, port=target.port, path=path
            )
            request.headers["host"] = f"{target.host}:{target.port}"
        return await original(self, request, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)


def _configure_backend(monkeypatch, fakes_url: str) -> None:
    """Настройки backend под стенд. Часть значений модули читают при импорте,
    поэтому подменяются и их копии."""
    import yookassa

    import bot_project
    from api.v1 import landing, payments
    from app import factories, rate_limit
    from client import _xui_circuit_breaker
    from config import settings
    from services.core.connect_module.repositories import form_data
    from services.core.keys.utils import inbounds
    from services.core.tariff import data as tariff_data

    for attr, value in {
        "api_url": fakes_url,
        "xui_subscription_url": f"{fakes_url}/sub",
        "external_subscription_url": "",
        "xui_inbound_id_landing": LANDING_INBOUND,
        "bot_token": BOT_TOKEN,
        "landing_internal_secret": LANDING_INTERNAL_SECRET,
        "disable_webhook_ip_check": True,
        "yookassa_shop_id": "000000",
        "yookassa_secret_key": "test_integration",
    }.items():
        monkeypatch.setattr(settings, attr, value)

    monkeypatch.setattr(inbounds, "BASELINE_INBOUNDS", [LANDING_INBOUND])
    monkeypatch.setattr(inbounds, "PAID_OVERLAY_INBOUNDS", list(PAID_INBOUNDS))
    monkeypatch.setattr(inbounds, "_TRIAL_TARIFF_ID", TRIAL_TARIFF_ID)
    monkeypatch.setattr(form_data, "LIST_AVAILABLE_CONNECTIONS", list(PAID_INBOUNDS))
    monkeypatch.setattr(tariff_data, "AVAILABLE_RATES_LIST", list(PAID_TARIFF_IDS))
    monkeypatch.setattr(landing, "DEFAULT_PRICING_PLAN", str(TRIAL_TARIFF_ID))
    monkeypatch.setattr(payments._NOTIFIER, "_bot_token", BOT_TOKEN)
    monkeypatch.setattr(bot_project.bot, "_token", BOT_TOKEN)
    monkeypatch.setattr(bot_project.bot, "_username_cache", None)
    monkeypatch.setattr(yookassa.Configuration, "api_url", f"{fakes_url}/v3")
    _redirect_telegram(monkeypatch, fakes_url)
    # Предохранитель панели, лимиты запросов и троттлинг проверок ЮKassa живут в памяти
    # процесса: состояние одного теста не должно влиять на другой
    _xui_circuit_breaker.close()
    rate_limit._buckets.clear()
    payments._yk_cancel_checked_at.clear()
    factories.promo_failed_attempts().clear()


async def _seed(pool, fakes_url: str) -> None:
    from config import settings

    await pool.executemany(
        "INSERT INTO tariff (id, name_tariff, amount, description, limit_ip, period, traffic_limit)"
        " VALUES ($1, $2, $3, $4, $5, $6, 0)",
        TARIFFS,
    )
    await pool.execute("SELECT setval('tariff_id_seq', (SELECT max(id) FROM tariff))")
    await pool.execute(
        "INSERT INTO servers (id, cluster_name, server_name, api_url, subscription_url, login, password)"
        " VALUES ($1, 'test', 'main', $2, $3, 'test', 'test')",
        settings.xui_server_id,
        fakes_url,
        f"{fakes_url}/sub",
    )


@dataclass
class Backend:
    """Приложение на тестовой базе и заглушках; ``client`` ходит в него как bot/web."""

    client: httpx.AsyncClient
    pool: asyncpg.Pool
    fakes: Fakes
    app: Any

    async def reload_cache(self) -> None:
        """Перечитать кеш из БД — как периодическая синхронизация после правок в обход API."""
        from database.service import DataService
        from services.cache.loader import LoadingService

        await LoadingService(cache=self.app.state.cache, data_service=DataService(), pool=self.pool).loading()


@pytest.fixture
async def backend(pool, fakes, monkeypatch):
    from app.main import app
    from config import settings
    from database.service import DataService
    from services.cache.loader import LoadingService
    from services.cache.service import CacheService
    from services.cache.storage import CacheStorage
    from services.core.data.service import ServiceDataModel

    _configure_backend(monkeypatch, fakes.url)
    await _seed(pool, fakes.url)

    # То же, что lifespan в app/main.py, без планировщика
    storage = CacheStorage()
    await storage.start()
    cache = CacheService(storage)
    data_service = DataService()
    await LoadingService(cache=cache, data_service=data_service, pool=pool).loading()
    app.dependency_overrides.clear()
    app.state.pool = pool
    app.state.cache = cache
    app.state.service_data = ServiceDataModel(cache_service=cache, data_service=data_service)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Bot-Secret": settings.bot_secret_key}
    ) as client:
        yield Backend(client=client, pool=pool, fakes=fakes, app=app)
    await storage.stop()
