"""Фикстуры e2e: один стенд на всю сессию pytest, браузер Playwright, база."""
from __future__ import annotations

import asyncio
import itertools
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterator

import asyncpg
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import Fakes, sign_init_data, telegram_webapp_stub  # noqa: E402
from stack import ADMIN_TG_ID, Stack, StackConfig  # noqa: E402

TELEGRAM_JS = "https://telegram.org/js/telegram-web-app.js"
_tg_ids = itertools.count(int(os.environ.get("E2E_FIRST_TG_ID", "710000001")))
_client_ips = (f"198.51.100.{i}" for i in itertools.count(1))  # TEST-NET-2


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    s = Stack(StackConfig.from_env()).start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture(scope="session")
def fakes(stack: Stack) -> Fakes:
    return Fakes(stack.cfg.fakes_url)


@pytest.fixture
def new_tg_id() -> int:
    """Свой tg_id на каждый тест: тесты не делят пользователей."""
    return next(_tg_ids)


@pytest.fixture(scope="session")
def admin_tg_id() -> int:
    return ADMIN_TG_ID


class Db:
    def __init__(self, url: str) -> None:
        self.url = url

    def fetch(self, query: str, *args: Any) -> list[dict]:
        async def run() -> list[dict]:
            conn = await asyncpg.connect(self.url)
            try:
                return [dict(r) for r in await conn.fetch(query, *args)]
            finally:
                await conn.close()

        # Playwright (sync API) держит свой event loop в этом потоке — asyncpg в отдельном
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, run()).result()

    def keys_of(self, tg_id: int) -> list[dict]:
        return self.fetch("SELECT * FROM keys WHERE tg_id = $1 ORDER BY created_at", tg_id)

    def payments_of(self, tg_id: int) -> list[dict]:
        return self.fetch("SELECT * FROM payments WHERE tg_id = $1 ORDER BY created_at", tg_id)


@pytest.fixture(scope="session")
def db(stack: Stack) -> Db:
    return Db(stack.cfg.database_url())


@pytest.fixture(scope="session")
def backend(stack: Stack) -> Iterator[httpx.Client]:
    """Прямой доступ к backend с секретом бота — как это делает бот."""
    with httpx.Client(
        base_url=f"{stack.cfg.backend_url}/api/v1",
        headers={"X-Bot-Secret": stack.secrets.bot_secret},
        timeout=60,
        trust_env=False,
    ) as client:
        yield client


# --- браузер -----------------------------------------------------------------


@pytest.fixture(scope="session")
def browser(stack: Stack):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=os.environ.get("E2E_HEADED") != "1")
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def context(browser, stack: Stack, request):
    # Свой адрес клиента на каждый тест (через «внешний прокси» 127.0.0.1):
    # лимиты nginx и backend считаются по IP, а в проде пользователи приходят с разных.
    ctx = browser.new_context(
        locale="ru-RU",
        viewport={"width": 420, "height": 900},
        extra_http_headers={"X-Forwarded-For": next(_client_ips)},
    )
    # Наружу браузер не ходит: всё, кроме стенда, отклоняется (скрипт Telegram
    # подменяется отдельно в telegram_page).
    allowed = re.compile(r"^https?://(127\.0\.0\.1|localhost|[a-z0-9-]+\.localhost)(:\d+)?/")
    ctx.route(
        re.compile(r".*"),
        lambda route: route.continue_() if allowed.match(route.request.url) else route.abort(),
    )
    ctx.set_default_timeout(15_000)
    yield ctx
    rep = getattr(request.node, "rep_call", None)
    if rep is not None and rep.failed:
        shots = stack.cfg.artifacts / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        for i, page in enumerate(ctx.pages):
            try:
                page.screenshot(path=str(shots / f"{request.node.name}-{i}.png"), full_page=True)
            except Exception:
                pass
    ctx.close()


@pytest.fixture
def page(context):
    return context.new_page()


@pytest.fixture
def telegram_page(context):
    """Страница внутри «Telegram»: скрипт telegram-web-app.js отдаёт заглушку с initData."""

    def open_page(init_data: str):
        p = context.new_page()
        p.route(TELEGRAM_JS, lambda route: route.fulfill(
            status=200, content_type="application/javascript", body=telegram_webapp_stub(init_data)))
        return p

    return open_page


@pytest.fixture
def sign(stack: Stack):
    """sign(tg_id, bot="main"|"admin", **kw) -> initData."""

    def _sign(tg_id: int, bot: str = "main", **kwargs) -> str:
        token = stack.secrets.bot_token if bot == "main" else stack.secrets.admin_bot_token
        return sign_init_data(token, tg_id, **kwargs)

    return _sign


@pytest.fixture
def web_login(stack: Stack, fakes: Fakes):
    """Вход на сайт через бота, как это делает человек: кнопка на сайте →
    /start weblogin_<token> в боте → «Это я, войти» → сайт открывает кабинет."""
    from playwright.sync_api import expect

    def _login(page, tg_id: int, *, open_site: bool = True) -> str:
        if open_site:
            page.goto(stack.cfg.web_url)
        page.get_by_role("button", name="Войти через бота").click()
        link = page.get_by_role("link", name="Открыть бота")
        href = link.get_attribute("href") or ""
        assert href.startswith("https://t.me/e2e_vpn_bot?start=weblogin_"), href
        payload = href.split("start=", 1)[1]
        after = fakes.user_says(tg_id, f"/start {payload}")
        ask = fakes.wait_text(tg_id, "Вход на сайт", after=after)
        after = fakes.press(tg_id, ask, "Это я, войти")
        confirmed = fakes.wait_text(tg_id, "Вход подтверждён", after=after)
        expect(page.get_by_role("heading", name="Ключи")).to_be_visible(timeout=20_000)
        return confirmed["params"]["text"]

    return _login


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
