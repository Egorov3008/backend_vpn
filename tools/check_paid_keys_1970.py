"""Проверка: есть ли в панели 3x-UI ключи с платным тарифом и временем истечения 1970 года.

Сравнивает standalone-клиентов из панели 3x-UI с ключами в БД. Ключ считается
платным, если его тариф (или сам ключ) имеет amount > 0. Время истечения 1970
года — это expiry_time близко к unix epoch (0 <= expiry_time_ms < 24ч после
эпохи). Результат печатается в stdout.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import asyncpg
import httpx
from dotenv import load_dotenv


@dataclass(frozen=True)
class PanelClient:
    email: str
    client_id: str
    tg_id: int
    expiry_time: int  # ms
    inbound_ids: list[int]
    enable: bool


@dataclass(frozen=True)
class KeyRow:
    email: str
    client_id: str
    tg_id: int
    tariff_id: Optional[int]
    amount: Optional[float]
    expiry_time: int  # ms


@dataclass(frozen=True)
class TariffRow:
    id: int
    name_tariff: str
    amount: float


def _load_env() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
    # также попробуем корневой .env для compose-окружения
    root_env = Path(__file__).parent.parent.parent / ".env"
    if root_env.exists():
        load_dotenv(root_env, override=False)


def _web_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _api_base_url(base_url: str) -> str:
    """3x-ui v3.2.0 раздаёт standalone API по префиксу <webBasePath>/panel/."""
    return base_url.rstrip("/") + "/panel"


def _resolve_local_db_url(dsn: str) -> str:
    """Если DSN указывает на docker-хост postgres:5432, переключаем на 127.0.0.1:5433.

    При запуске внутри контейнера оригинальный DSN остаётся рабочим, а с хоста
    локальный порт контейнера PostgreSQL обычно проброшен на 127.0.0.1:5433.
    """
    parsed = urlparse(dsn)
    if not parsed.hostname or not parsed.port:
        return dsn
    if parsed.hostname in ("127.0.0.1", "localhost"):
        return dsn
    # если хост не локальный, но порт 5432 — заменяем на localhost:5433
    if parsed.port == 5432:
        new_netloc = f"{parsed.username}:{parsed.password}@127.0.0.1:5433"
        parsed = parsed._replace(netloc=new_netloc)
        return urlunparse(parsed)
    return dsn


def _int_ms(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _panel_clients_from_response(data: Any) -> list[PanelClient]:
    clients: list[PanelClient] = []
    if isinstance(data, dict):
        data = data.get("obj", []) or []
    if not isinstance(data, list):
        return clients
    for raw in data:
        if not isinstance(raw, dict):
            continue
        email = (raw.get("email") or "").strip()
        if not email:
            continue
        inbound_ids_raw = raw.get("inboundIds") or []
        inbound_ids = [int(x) for x in inbound_ids_raw if isinstance(x, (int, str))]
        clients.append(
            PanelClient(
                email=email,
                client_id=str(raw.get("id", "")),
                tg_id=int(raw.get("tgId") or raw.get("tg_id") or 0),
                expiry_time=_int_ms(raw.get("expiryTime") or raw.get("expiry_time")),
                inbound_ids=inbound_ids,
                enable=bool(raw.get("enable", True)),
            )
        )
    return clients


async def _auth_xui_with_login(
    client: httpx.AsyncClient, web_base: str, username: str, password: str
) -> None:
    web_base = web_base.rstrip("/")

    # 1. CSRF-токен и стартовая сессия
    csrf_resp = await client.get(
        f"{web_base}/csrf-token",
        headers={"Accept": "application/json"},
    )
    csrf_resp.raise_for_status()
    csrf_data = csrf_resp.json()
    csrf_token = csrf_data.get("obj") or csrf_data.get("csrfToken")
    if not csrf_token:
        raise RuntimeError(f"Не удалось получить CSRF token: {csrf_data}")

    session_cookie = None
    for cookie_name in ("session", "3x-ui"):
        cookie = csrf_resp.cookies.get(cookie_name)
        if cookie:
            session_cookie = cookie
            break

    # 2. Логин
    login_resp = await client.post(
        f"{web_base}/login",
        data={"username": username, "password": password},
        headers={"X-CSRF-Token": csrf_token},
    )
    login_resp.raise_for_status()
    login_data = login_resp.json()
    if not login_data.get("success"):
        raise RuntimeError(f"Логин в панель не удался: {login_data}")

    for cookie_name in ("session", "3x-ui"):
        cookie = login_resp.cookies.get(cookie_name)
        if cookie:
            session_cookie = cookie
            break

    if not session_cookie:
        raise RuntimeError("Не удалось получить session cookie после логина")

    client.cookies.set("session", session_cookie)


async def fetch_panel_clients(
    base_url: str,
    username: str,
    password: str,
    token: Optional[str] = None,
    verify_ssl: bool = True,
) -> list[PanelClient]:
    web_base = _web_base_url(base_url)
    api_base = _api_base_url(base_url)
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
        if not token:
            await _auth_xui_with_login(client, web_base, username, password)

        resp = await client.get(f"{api_base}/api/clients/list", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return _panel_clients_from_response(data)


async def fetch_db_data(pool: asyncpg.Pool) -> tuple[list[KeyRow], list[TariffRow]]:
    async with pool.acquire() as conn:
        keys_records = await conn.fetch(
            "SELECT tg_id, client_id, email, tariff_id, amount, expiry_time FROM keys"
        )
        tariff_records = await conn.fetch(
            "SELECT id, name_tariff, amount FROM tariff"
        )

    keys = [
        KeyRow(
            email=r["email"],
            client_id=r["client_id"],
            tg_id=r["tg_id"],
            tariff_id=r.get("tariff_id"),
            amount=float(r["amount"]) if r.get("amount") is not None else None,
            expiry_time=_int_ms(r["expiry_time"]),
        )
        for r in keys_records
    ]
    tariffs = [
        TariffRow(
            id=r["id"],
            name_tariff=r["name_tariff"],
            amount=float(r["amount"]),
        )
        for r in tariff_records
    ]
    return keys, tariffs


def _format_ms(dt_ms: int) -> str:
    if dt_ms <= 0:
        return "0 (1970-01-01 00:00 UTC)"
    dt = datetime.fromtimestamp(dt_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _is_paid(key: KeyRow, tariffs: dict[int, TariffRow]) -> tuple[bool, Optional[str]]:
    tariff = tariffs.get(key.tariff_id) if key.tariff_id is not None else None
    if tariff and tariff.amount > 0:
        return True, tariff.name_tariff
    if key.amount is not None and key.amount > 0:
        return True, f"key.amount={key.amount}"
    return False, None


def _is_1970_expiry(expiry_ms: int) -> bool:
    # 1970 год в unix-миллисекундах: 0 <= ms < 365*24*60*60*1000 (≈ 31_536_000_000)
    # Берём порог в 24 часа после эпохи — если expiry_time = 0, это явно "1970-01-01".
    # Если нужен весь 1970 год, замените THRESHOLD на 31_536_000_000.
    THRESHOLD_MS = 86_400_000  # 24 часа
    return 0 <= expiry_ms < THRESHOLD_MS


async def main() -> None:
    _load_env()

    database_url = os.environ.get("DATABASE_URL")
    xui_api_url = os.environ.get("XUI_API_URL", "")
    xui_login = os.environ.get("XUI_LOGIN", "")
    xui_password = os.environ.get("XUI_PASSWORD", "")
    xui_token = os.environ.get("XUI_TOKEN") or os.environ.get("XUI_API_TOKEN")
    xui_skip_ssl = os.environ.get("XUI_SKIP_SSL_VERIFY", "false").lower() in ("true", "1", "yes")

    if not database_url:
        raise RuntimeError("DATABASE_URL не найден в .env")
    if not xui_api_url or not xui_login or not xui_password:
        raise RuntimeError("XUI_API_URL / XUI_LOGIN / XUI_PASSWORD не найдены в .env")

    database_url = _resolve_local_db_url(database_url)

    print(f"Панель: {xui_api_url}")
    print(f"API base: {_api_base_url(xui_api_url)}")
    print(f"SSL verify: {not xui_skip_ssl}")
    print(f"Auth: {'Bearer token' if xui_token else 'login+cookie'}")
    print("Получаю клиентов из панели 3x-UI...")
    panel_clients = await fetch_panel_clients(
        xui_api_url,
        xui_login,
        xui_password,
        token=xui_token,
        verify_ssl=not xui_skip_ssl,
    )
    print(f"Клиентов в панели: {len(panel_clients)}")

    print("Подключаюсь к БД...")
    pool = await asyncpg.create_pool(database_url)
    try:
        keys, tariffs = await fetch_db_data(pool)
    finally:
        await pool.close()
    print(f"Ключей в БД: {len(keys)}, тарифов в БД: {len(tariffs)}")

    tariff_by_id = {t.id: t for t in tariffs}
    keys_by_email = {k.email: k for k in keys}

    matched_paid_1970: list[tuple[PanelClient, KeyRow, str]] = []
    panel_not_in_db: list[PanelClient] = []
    paid_but_not_1970: list[tuple[PanelClient, KeyRow, str]] = []

    for client in panel_clients:
        key = keys_by_email.get(client.email)
        if key is None:
            panel_not_in_db.append(client)
            continue

        paid, tariff_name = _is_paid(key, tariff_by_id)
        if not paid:
            continue

        if _is_1970_expiry(client.expiry_time):
            matched_paid_1970.append((client, key, tariff_name))
        else:
            paid_but_not_1970.append((client, key, tariff_name))

    print()
    print("=" * 80)
    if not matched_paid_1970:
        print("Ключей с платным тарифом и истечением 1970 года НЕ НАЙДЕНО.")
    else:
        print(f"НАЙДЕНО {len(matched_paid_1970)} ключ(ей) с платным тарифом и истечением 1970 года:")
        print()
        for idx, (client, key, tariff_name) in enumerate(matched_paid_1970, start=1):
            print(f"{idx}. email:        {client.email}")
            print(f"   client_id:    {client.client_id}")
            print(f"   tg_id:        {client.tg_id}")
            print(f"   панель enable:{client.enable}")
            print(f"   inbound_ids:  {client.inbound_ids}")
            print(f"   tariff_id:    {key.tariff_id}")
            print(f"   tariff_name:  {tariff_name}")
            print(f"   panel expiry: {client.expiry_time} ms -> {_format_ms(client.expiry_time)}")
            print(f"   db expiry:    {key.expiry_time} ms -> {_format_ms(key.expiry_time)}")
            print("-" * 40)

    print()
    print("Сводка:")
    print(f"  - Всего клиентов в панели: {len(panel_clients)}")
    print(f"  - Из них нет в БД keys:      {len(panel_not_in_db)}")
    print(f"  - Платные, но НЕ 1970:      {len(paid_but_not_1970)}")
    print(f"  - Платные И 1970 (найдено): {len(matched_paid_1970)}")
    print("=" * 80)

    # Покажем первые 5 клиентов панели без записи в БД для диагностики
    if panel_not_in_db:
        print()
        print("Примеры клиентов панели, отсутствующих в БД keys:")
        for client in panel_not_in_db[:5]:
            print(f"  - {client.email} (tg_id={client.tg_id}, expiry={_format_ms(client.expiry_time)})")


if __name__ == "__main__":
    asyncio.run(main())
