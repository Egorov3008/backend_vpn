"""Локальный стенд для e2e: Postgres-база, заглушки, backend, бот, nginx веба и мини-аппа.

Всё запускается процессами на 127.0.0.1, без docker. Секреты генерируются
заново на каждый запуск и никуда не записываются, кроме окружения процессов.

Ручной запуск (стенд живёт, пока не нажать Ctrl+C):

    python e2e/stack.py

Пути и параметры — переменные окружения (см. ``StackConfig.from_env``).
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

E2E_DIR = Path(__file__).resolve().parent
BACKEND_REPO = E2E_DIR.parent
WORKSPACE = BACKEND_REPO.parent

MAIN_BOT = {"id": 7000000001, "is_bot": True, "first_name": "VPN e2e", "username": "e2e_vpn_bot"}
ADMIN_BOT = {"id": 7000000002, "is_bot": True, "first_name": "Admin e2e", "username": "e2e_admin_bot"}
ADMIN_TG_ID = 900001
PAID_INBOUNDS = [11, 12]

# Тарифы стенда как в проде: 10 — триал (id 10 зашит в backend), 7 и 8 — платные.
TRIAL_TARIFF_ID = 10
PAID_TARIFF_IDS = [7, 8]
TARIFFS = [
    (TRIAL_TARIFF_ID, "Пробный", 0.0, "Бесплатный пробный период", 1, 7),
    (7, "Стандарт", 199.0, "1 устройство", 1, 30),
    (8, "Семейный", 349.0, "До 3 устройств", 3, 30),
]


def _venv_python(repo: Path) -> str:
    candidate = repo / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


@dataclass
class StackConfig:
    db_admin_url: str
    db_name: str
    bot_repo: Path
    web_repo: Path
    miniapp_repo: Path
    backend_python: str
    bot_python: str
    fakes_python: str
    nginx: str
    host: str = "127.0.0.1"
    fakes_port: int = 19100
    backend_port: int = 18000
    web_port: int = 18080
    miniapp_port: int = 18090
    build_frontends: bool = True
    with_bot: bool = True
    artifacts: Path = field(default_factory=lambda: E2E_DIR / ".artifacts")

    @classmethod
    def from_env(cls) -> "StackConfig":
        env = os.environ
        bot_repo = Path(env.get("E2E_BOT_REPO", WORKSPACE / "plaltform-bot"))
        return cls(
            # Сервер Postgres, где можно создать/удалить отдельную базу стенда.
            db_admin_url=env.get("E2E_DATABASE_URL", "postgresql://e2e:e2e@127.0.0.1:5432/postgres"),
            db_name=env.get("E2E_DB_NAME", "vpn_e2e"),
            bot_repo=bot_repo,
            web_repo=Path(env.get("E2E_WEB_REPO", WORKSPACE / "platform-web")),
            miniapp_repo=Path(env.get("E2E_MINIAPP_REPO", WORKSPACE / "vpn_miniapp")),
            backend_python=env.get("E2E_BACKEND_PYTHON", _venv_python(BACKEND_REPO)),
            bot_python=env.get("E2E_BOT_PYTHON", _venv_python(bot_repo)),
            fakes_python=sys.executable,
            nginx=env.get("E2E_NGINX", shutil.which("nginx") or "nginx"),
            build_frontends=env.get("E2E_SKIP_BUILD", "") != "1",
            with_bot=env.get("E2E_WITHOUT_BOT", "") != "1",
        )

    @property
    def fakes_url(self) -> str:
        return f"http://{self.host}:{self.fakes_port}"

    @property
    def backend_url(self) -> str:
        return f"http://{self.host}:{self.backend_port}"

    @property
    def web_url(self) -> str:
        # localhost, а не 127.0.0.1: админка живёт на admin.localhost того же порта,
        # Chromium резолвит *.localhost в loopback сам.
        return f"http://localhost:{self.web_port}"

    @property
    def admin_host(self) -> str:
        return "admin.localhost"

    @property
    def admin_url(self) -> str:
        return f"http://{self.admin_host}:{self.web_port}"

    @property
    def miniapp_url(self) -> str:
        return f"http://localhost:{self.miniapp_port}"

    def database_url(self) -> str:
        parts = urlsplit(self.db_admin_url)
        return urlunsplit(parts._replace(path=f"/{self.db_name}"))


@dataclass
class Secrets:
    bot_secret: str = field(default_factory=lambda: secrets.token_hex(24))
    admin_api_key: str = field(default_factory=lambda: secrets.token_hex(24))
    invite_token: str = field(default_factory=lambda: secrets.token_hex(16))
    mvp_app_secret: str = field(default_factory=lambda: secrets.token_hex(16))
    session_secret: str = field(default_factory=lambda: secrets.token_hex(32))
    landing_internal: str = field(default_factory=lambda: secrets.token_hex(24))
    landing_cookie: str = field(default_factory=lambda: secrets.token_hex(24))
    bot_token: str = field(default_factory=lambda: f"{MAIN_BOT['id']}:{secrets.token_urlsafe(26)}")
    admin_bot_token: str = field(default_factory=lambda: f"{ADMIN_BOT['id']}:{secrets.token_urlsafe(26)}")


def _clean_env() -> dict[str, str]:
    """Окружение без прокси: стенд не должен ходить наружу ни при каких условиях."""
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    env["NO_PROXY"] = env["no_proxy"] = "127.0.0.1,localhost,.localhost"
    env["PYTHONUNBUFFERED"] = "1"
    return env


class Stack:
    def __init__(self, cfg: StackConfig) -> None:
        self.cfg = cfg
        self.secrets = Secrets()
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs = cfg.artifacts / "logs"
        self.tmp = Path(tempfile.mkdtemp(prefix="vpn-e2e-"))

    # --- окружения ---------------------------------------------------------

    def backend_env(self) -> dict[str, str]:
        c, s = self.cfg, self.secrets
        env = _clean_env()
        env.update(
            DATABASE_URL=c.database_url(),
            BOT_SECRET_KEY=s.bot_secret,
            ADMIN_API_KEY=s.admin_api_key,
            INVITE_TOKEN=s.invite_token,
            MVP_APP_SECRET=s.mvp_app_secret,
            MINIAPP_SESSION_SECRET=s.session_secret,
            LANDING_INTERNAL_SECRET=s.landing_internal,
            LANDING_COOKIE_SECRET=s.landing_cookie,
            XUI_API_URL=c.fakes_url,
            XUI_LOGIN="e2e",
            XUI_PASSWORD=secrets.token_hex(8),
            XUI_SUB=f"{c.fakes_url}/sub",
            EXTERNAL_SUB_URL="",
            AVAILABLE_CONNECTIONS=json.dumps(PAID_INBOUNDS),
            AVAILABLE_RATES=json.dumps(PAID_TARIFF_IDS),
            DEFAULT_PRICING_PLAN=str(TRIAL_TARIFF_ID),
            YOOKASSA_SHOP_ID="000000",
            YOOKASSA_SECRET_KEY=f"test_{secrets.token_hex(8)}",
            DISABLE_WEBHOOK_IP_CHECK="true",
            WEBHOOK_BASE_URL=c.backend_url,
            BOT_TOKEN=s.bot_token,
            BOT_NAME="",
            ADMIN_BOT_TOKEN=s.admin_bot_token,
            ADMIN_ID=json.dumps([ADMIN_TG_ID]),
            ADMIN_TG_IDS=json.dumps([ADMIN_TG_ID]),
            WEB_PUBLIC_URL=c.web_url,
            WEB_COOKIE_SECURE="false",
            LANDING_PUBLIC_URL=c.web_url,
            CANALL_URL="https://t.me/e2e_channel",
            SUPPORT_CHAT_URL="https://t.me/e2e_support",
            MINIAPP_URL=c.miniapp_url,
            DAILY_PAYMENTS_REPORT_ENABLED="false",
            LOG_LEVEL="INFO",
            E2E_FAKES_URL=c.fakes_url,
            E2E_BACKEND_PORT=str(c.backend_port),
        )
        return env

    def bot_env(self) -> dict[str, str]:
        c, s = self.cfg, self.secrets
        env = _clean_env()
        env.update(
            BOT_TOKEN=s.bot_token,
            BOT_NAME="",
            ADMIN_ID=json.dumps([ADMIN_TG_ID]),
            AVAILABLE_CONNECTIONS=json.dumps(PAID_INBOUNDS),
            AVAILABLE_RATES=json.dumps(PAID_TARIFF_IDS),
            DEFAULT_PRICING_PLAN=str(TRIAL_TARIFF_ID),
            PAYMENT_INFO="{}",
            BACKEND_URL=c.backend_url,
            BOT_SECRET_KEY=s.bot_secret,
            ADMIN_API_KEY=s.admin_api_key,
            INVITE_TOKEN=s.invite_token,
            LANDING_INTERNAL_SECRET=s.landing_internal,
            MINIAPP_URL=c.miniapp_url,
            LANDING_PUBLIC_URL=c.web_url,
            CANALL_URL="https://t.me/e2e_channel",
            CHANNEL_URL="https://t.me/e2e_channel",
            SUPPORT_CHAT_URL="https://t.me/e2e_support",
            E2E_FAKES_URL=c.fakes_url,
        )
        return env

    # --- шаги --------------------------------------------------------------

    def start(self) -> "Stack":
        self.logs.mkdir(parents=True, exist_ok=True)
        try:
            self._prepare_database()
            self._spawn("fakes", [self.cfg.fakes_python, str(E2E_DIR / "fakes.py"), "--port", str(self.cfg.fakes_port)],
                        _clean_env(), cwd=E2E_DIR)
            self._wait_http(f"{self.cfg.fakes_url}/_ctl/state", "fakes")
            self.reset_fakes()
            self._spawn("backend", [self.cfg.backend_python, str(E2E_DIR / "run_backend.py")], self.backend_env(),
                        cwd=BACKEND_REPO)
            self._wait_http(f"{self.cfg.backend_url}/health", "backend", timeout=60)
            if self.cfg.with_bot:
                self._spawn("bot", [self.cfg.bot_python, str(E2E_DIR / "run_bot.py"), str(self.cfg.bot_repo)],
                            self.bot_env(), cwd=self.cfg.bot_repo)
                self._wait_log("bot", "Run polling for bot", timeout=90)
            self._start_nginx()
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self) -> None:
        for name, proc in reversed(list(self.procs.items())):
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM if name != "nginx" else signal.SIGQUIT)
        deadline = time.time() + 10
        for proc in self.procs.values():
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def reset_fakes(self) -> None:
        httpx.post(
            f"{self.cfg.fakes_url}/_ctl/reset",
            json={
                "bots": {self.secrets.bot_token: MAIN_BOT, self.secrets.admin_bot_token: ADMIN_BOT},
                "inbounds": PAID_INBOUNDS,
                "webhook_url": f"{self.cfg.backend_url}/api/v1/payments/webhook",
            },
            timeout=5,
        ).raise_for_status()

    def _prepare_database(self) -> None:
        """Чистая база стенда: схема из db/schema_fixed.sql + тарифы."""
        if "e2e" not in self.cfg.db_name:
            # База пересоздаётся: защита от опечатки с именем рабочей базы
            raise RuntimeError(f"E2E_DB_NAME должен содержать 'e2e', а не {self.cfg.db_name!r}")
        import asyncio

        import asyncpg

        async def run() -> None:
            admin = await asyncpg.connect(self.cfg.db_admin_url)
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{self.cfg.db_name}" WITH (FORCE)')
                await admin.execute(f'CREATE DATABASE "{self.cfg.db_name}"')
            finally:
                await admin.close()
            conn = await asyncpg.connect(self.cfg.database_url())
            try:
                await conn.execute((BACKEND_REPO / "db" / "schema_fixed.sql").read_text())
                await conn.executemany(
                    "INSERT INTO tariff (id, name_tariff, amount, description, limit_ip, period, traffic_limit)"
                    " VALUES ($1, $2, $3, $4, $5, $6, 0)",
                    TARIFFS,
                )
                await conn.execute("SELECT setval('tariff_id_seq', (SELECT max(id) FROM tariff))")
            finally:
                await conn.close()

        asyncio.run(run())

    def _build(self, repo: Path) -> Path:
        dist = repo / "dist"
        if self.cfg.build_frontends or not dist.exists():
            if not (repo / "node_modules").exists():
                subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=repo, check=True,
                               stdout=subprocess.DEVNULL)
            env = {**os.environ, "VITE_BOT_USERNAME": MAIN_BOT["username"]}
            subprocess.run(["npm", "run", "build"], cwd=repo, check=True, env=env, stdout=subprocess.DEVNULL)
        return dist

    def _start_nginx(self) -> None:
        """Настоящие nginx-конфиги веба и мини-аппа, адаптированные под процессы.

        Меняются только адреса (порт, backend, пути к статике и сниппетам), правила
        location и заголовки остаются как в проде."""
        c = self.cfg
        web_dist = self._build(c.web_repo)
        mini_dist = self._build(c.miniapp_repo)
        snippets = self.tmp / "snippets"
        snippets.mkdir()
        for name in ("security-headers.conf", "security-headers-admin.conf"):
            shutil.copy(c.web_repo / name, snippets / name)

        backend = f"{c.host}:{c.backend_port}"
        web = (c.web_repo / "nginx.conf.template").read_text()
        web = (
            web.replace("${ADMIN_HOST}", c.admin_host)
            .replace("listen 80 default_server;", f"listen {c.host}:{c.web_port} default_server;")
            .replace("listen 80;", f"listen {c.host}:{c.web_port};")
            .replace("http://backend:8000", f"http://{backend}")
            .replace("/etc/nginx/snippets/", f"{snippets}/")
            .replace("root /usr/share/nginx/html;", f"root {web_dist};")
            # 127.0.0.1 играет роль внешнего TLS-прокси: браузер каждого теста
            # приходит со своим X-Forwarded-For, как разные пользователи в проде.
            .replace("set_real_ip_from 10.0.0.0/8;", "set_real_ip_from 127.0.0.1;\nset_real_ip_from 10.0.0.0/8;")
        )
        mini = (c.miniapp_repo / "nginx.conf").read_text()
        mini = (
            mini.replace("listen 80;", f"listen {c.host}:{c.miniapp_port};")
            .replace("http://backend:8000", f"http://{backend}")
            .replace("root /usr/share/nginx/html;", f"root {mini_dist};")
        )
        for text, port in ((web, c.web_port), (mini, c.miniapp_port)):
            if f":{port}" not in text:
                raise RuntimeError("nginx-конфиг изменился: не удалось подставить порт")
        (self.tmp / "web.conf").write_text(web)
        (self.tmp / "miniapp.conf").write_text(mini)
        (self.tmp / "logs").mkdir()
        mime = Path("/etc/nginx/mime.types")
        (self.tmp / "nginx.conf").write_text(
            f"""
pid {self.tmp}/nginx.pid;
error_log {self.logs}/nginx-error.log warn;
events {{ worker_connections 256; }}
http {{
    {"include " + str(mime) + ";" if mime.exists() else ""}
    default_type application/octet-stream;
    access_log {self.logs}/nginx-access.log;
    client_body_temp_path {self.tmp}/body;
    proxy_temp_path {self.tmp}/proxy;
    fastcgi_temp_path {self.tmp}/fastcgi;
    uwsgi_temp_path {self.tmp}/uwsgi;
    scgi_temp_path {self.tmp}/scgi;
    include {self.tmp}/web.conf;
    include {self.tmp}/miniapp.conf;
}}
"""
        )
        self._spawn("nginx", [c.nginx, "-p", str(self.tmp), "-c", str(self.tmp / "nginx.conf"), "-g", "daemon off;"],
                    _clean_env(), cwd=self.tmp)
        self._wait_http(f"http://{c.host}:{c.web_port}/", "nginx")

    # --- процессы ----------------------------------------------------------

    def _spawn(self, name: str, cmd: list[str], env: dict[str, str], cwd: Path) -> None:
        log = open(self.logs / f"{name}.log", "w")
        self.procs[name] = subprocess.Popen(cmd, env=env, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)

    def _check_alive(self, name: str) -> None:
        proc = self.procs[name]
        if proc.poll() is not None:
            tail = (self.logs / f"{name}.log").read_text(errors="replace")[-3000:]
            raise RuntimeError(f"{name} завершился с кодом {proc.returncode}:\n{tail}")

    def _wait_http(self, url: str, name: str, timeout: float = 30) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._check_alive(name)
            try:
                if httpx.get(url, timeout=2, trust_env=False).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise TimeoutError(f"{name} не ответил на {url} за {timeout} с")

    def _wait_log(self, name: str, marker: str, timeout: float) -> None:
        deadline = time.time() + timeout
        path = self.logs / f"{name}.log"
        while time.time() < deadline:
            self._check_alive(name)
            if marker in path.read_text(errors="replace"):
                return
            time.sleep(0.5)
        raise TimeoutError(f"{name}: не дождались «{marker}» за {timeout} с")

    def log_text(self, name: str) -> str:
        path: Optional[Path] = self.logs / f"{name}.log"
        return path.read_text(errors="replace") if path and path.exists() else ""


def main() -> None:
    stack = Stack(StackConfig.from_env()).start()
    c = stack.cfg
    print(f"Кабинет:  {c.web_url}\nАдминка:  {c.admin_url}/admin\nМини-апп: {c.miniapp_url}\n"
          f"Backend:  {c.backend_url}\nЗаглушки: {c.fakes_url}/_ctl/state\nЛоги:     {stack.logs}")
    try:
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        stack.stop()


if __name__ == "__main__":
    main()
