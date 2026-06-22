"""Исправление expiry_time для платных ключей, 'застрявших' на 1970 году.

Логика:
1. Берёт ключи с платным тарифом и expiry_time == 0.
2. Для каждого ключа находит последний успешный платёж, привязанный к email
   (payment_type вида create_key|<email> или renew_key|<email>).
3. Новый expiry_time = created_at платежа + tariff.period * number_of_months.
4. Обновляет expiry_time в БД и на панели 3x-UI, устанавливает enable=True,
   сбрасывает notified_expired_grace = FALSE.

Скрипт идемпотентен: при повторном запуске для уже исправленных ключей
посчитанный expiry_time может оказаться в прошлом, но сам пересчёт не
зависит от текущего expiry_time.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

import asyncpg
import httpx
from dotenv import load_dotenv


@dataclass
class FixTarget:
    email: str
    client_id: str
    tg_id: int
    tariff_id: int
    tariff_period: int
    tariff_amount: float
    number_of_months: int
    payment_id: Optional[str]
    payment_created_at: datetime
    new_expiry_ms: int


def _load_env() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
    root_env = Path(__file__).parent.parent.parent / ".env"
    if root_env.exists():
        load_dotenv(root_env, override=False)


def _resolve_local_db_url(dsn: str) -> str:
    parsed = urlparse(dsn)
    if not parsed.hostname or not parsed.port:
        return dsn
    if parsed.hostname in ("127.0.0.1", "localhost"):
        return dsn
    if parsed.port == 5432:
        new_netloc = f"{parsed.username}:{parsed.password}@127.0.0.1:5433"
        parsed = parsed._replace(netloc=new_netloc)
        return urlunparse(parsed)
    return dsn


def _web_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _api_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/panel"


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


async def _auth_xui_with_login(
    client: httpx.AsyncClient, web_base: str, username: str, password: str
) -> None:
    web_base = web_base.rstrip("/")
    csrf_resp = await client.get(
        f"{web_base}/csrf-token",
        headers={"Accept": "application/json"},
    )
    csrf_resp.raise_for_status()
    csrf_data = csrf_resp.json()
    csrf_token = csrf_data.get("obj") or csrf_data.get("csrfToken")
    if not csrf_token:
        raise RuntimeError(f"Не удалось получить CSRF token: {csrf_data}")

    login_resp = await client.post(
        f"{web_base}/login",
        data={"username": username, "password": password},
        headers={"X-CSRF-Token": csrf_token},
    )
    login_resp.raise_for_status()
    login_data = login_resp.json()
    if not login_data.get("success"):
        raise RuntimeError(f"Логин в панель не удался: {login_data}")

    session_cookie = None
    for cookie_name in ("session", "3x-ui"):
        cookie = login_resp.cookies.get(cookie_name)
        if cookie:
            session_cookie = cookie
            break
    if not session_cookie:
        raise RuntimeError("Не удалось получить session cookie после логина")
    client.cookies.set("session", session_cookie)


async def _get_panel_client(
    base_url: str,
    username: str,
    password: str,
    token: Optional[str],
    email: str,
    verify_ssl: bool,
) -> Optional[dict]:
    web_base = _web_base_url(base_url)
    api_base = _api_base_url(base_url)
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
        if not token:
            await _auth_xui_with_login(client, web_base, username, password)
        resp = await client.get(f"{api_base}/api/clients/get/{email}", headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return None
        obj = data.get("obj")
        if isinstance(obj, dict) and "client" in obj:
            return obj.get("client")
        return obj


async def _update_panel_client(
    base_url: str,
    username: str,
    password: str,
    token: Optional[str],
    email: str,
    expiry_ms: int,
    limit_ip: int,
    enable: bool,
    verify_ssl: bool,
) -> bool:
    """Обновляет expiryTime и enable клиента на панели, сохраняя остальные поля."""
    web_base = _web_base_url(base_url)
    api_base = _api_base_url(base_url)
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    raw = await _get_panel_client(base_url, username, password, token, email, verify_ssl)
    if not raw:
        print(f"  [WARN] Клиент {email} не найден в панели, пропускаю обновление панели")
        return False

    payload = {
        "id": str(raw.get("id", "")),
        "email": raw.get("email", email),
        "tgId": raw.get("tgId") or raw.get("tg_id") or 0,
        "limitIp": limit_ip,
        "totalGB": raw.get("totalGB") or raw.get("total_gb") or 0,
        "expiryTime": expiry_ms,
        "enable": enable,
        "flow": raw.get("flow") or "xtls-rprx-vision",
        "subId": raw.get("subId") or raw.get("sub_id") or email,
        "group": raw.get("group", ""),
        "comment": raw.get("comment", ""),
    }
    inbound_ids = raw.get("inboundIds")
    if inbound_ids:
        payload["inboundIds"] = list(inbound_ids)

    async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
        if not token:
            await _auth_xui_with_login(client, web_base, username, password)
        resp = await client.post(
            f"{api_base}/api/clients/update/{email}",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        result = resp.json()
        if not result.get("success", True):
            print(f"  [WARN] Панель вернула ошибку: {result}")
            return False
        return True


async def _build_targets(pool: asyncpg.Pool) -> list[FixTarget]:
    async with pool.acquire() as conn:
        # платные тарифы
        tariff_rows = await conn.fetch("SELECT id, name_tariff, amount, period FROM tariff WHERE amount > 0")
        paid_tariff_ids = {r["id"]: {
            "name": r["name_tariff"],
            "amount": float(r["amount"]),
            "period": int(r["period"]),
        } for r in tariff_rows}

        # ключи с платным тарифом и expiry_time == 0
        keys = await conn.fetch(
            """
            SELECT k.tg_id, k.client_id, k.email, k.tariff_id, k.limit_ip, k.created_at
            FROM keys k
            WHERE k.tariff_id = ANY($1::int[])
              AND k.expiry_time = 0
            ORDER BY k.email
            """,
            list(paid_tariff_ids.keys()),
        )

        targets: list[FixTarget] = []
        for k in keys:
            email = k["email"]
            tariff_id = k["tariff_id"]
            tariff_meta = paid_tariff_ids[tariff_id]

            # последний succeeded-платёж, привязанный к этому email
            payment = await conn.fetchrow(
                """
                SELECT id, payment_id, payment_type, status, number_of_months, created_at
                FROM payments
                WHERE tg_id = $1::bigint
                  AND status = 'succeeded'
                  AND (
                    payment_type = 'create_key|' || $2
                    OR payment_type LIKE 'renew_key|' || $2
                  )
                ORDER BY created_at DESC
                LIMIT 1
                """,
                k["tg_id"],
                email,
            )

            if not payment:
                # Fallback: если нет платежа с точным email, пробуем последний succeeded
                # create_key|{tariff_id} этого пользователя — предполагаем, что это тот самый ключ.
                payment = await conn.fetchrow(
                    """
                    SELECT id, payment_id, payment_type, status, number_of_months, created_at
                    FROM payments
                    WHERE tg_id = $1::bigint
                      AND status = 'succeeded'
                      AND payment_type = 'create_key|' || $2
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    k["tg_id"],
                    str(k["tariff_id"]),
                )

            if not payment:
                print(f"  [WARN] Нет succeeded-платежа для {email} (tg_id={k['tg_id']}), пропускаю")
                continue

            number_of_months = int(payment["number_of_months"] or 1)
            created_at = payment["created_at"]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            # Если created_at платежа раньше created_at ключа — используем created_at ключа,
            # чтобы не получить expiry_time в прошлом (для create_key|tariff_id fallback).
            key_created_at = datetime.fromtimestamp(k.get("created_at", 0) / 1000, tz=timezone.utc)
            if key_created_at and key_created_at > created_at:
                created_at = key_created_at

            now = datetime.now(timezone.utc)
            if created_at < now:
                # Платёж уже в прошлом — отсчитываем срок от текущего момента,
                # чтобы пользователь не потерял оплаченный период из-за бага.
                created_at = now

            new_expiry = created_at + timedelta(days=tariff_meta["period"] * number_of_months)
            new_expiry_ms = int(new_expiry.timestamp() * 1000)

            targets.append(
                FixTarget(
                    email=email,
                    client_id=k["client_id"],
                    tg_id=k["tg_id"],
                    tariff_id=tariff_id,
                    tariff_period=tariff_meta["period"],
                    tariff_amount=tariff_meta["amount"],
                    number_of_months=number_of_months,
                    payment_id=payment["payment_id"],
                    payment_created_at=created_at,
                    new_expiry_ms=new_expiry_ms,
                )
            )
        return targets


async def _apply_fixes(
    pool: asyncpg.Pool,
    targets: list[FixTarget],
    base_url: str,
    username: str,
    password: str,
    token: Optional[str],
    verify_ssl: bool,
    dry_run: bool = True,
) -> None:
    for t in targets:
        new_dt = datetime.fromtimestamp(t.new_expiry_ms / 1000, tz=timezone.utc)
        action = "(dry-run)" if dry_run else ""
        print(
            f"\n{action} {t.email}: tg_id={t.tg_id}, tariff={t.tariff_id} "
            f"({t.tariff_amount}₽/{t.tariff_period}дн * {t.number_of_months}мес), "
            f"платёж {t.payment_id or '(id=' + str(t.payment_id) + ')' } "
            f"от {t.payment_created_at:%Y-%m-%d %H:%M UTC}, "
            f"новый expiry={new_dt:%Y-%m-%d %H:%M UTC} ({t.new_expiry_ms} ms)"
        )
        if dry_run:
            continue

        # Обновляем БД
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE keys
                SET expiry_time = $1,
                    notified_expired_grace = FALSE,
                    used_traffic = 0.0
                WHERE email = $2
                """,
                t.new_expiry_ms,
                t.email,
            )
            print(f"  БД UPDATE: {result.strip()}")

        # Обновляем панель
        updated = await _update_panel_client(
            base_url=base_url,
            username=username,
            password=password,
            token=token,
            email=t.email,
            expiry_ms=t.new_expiry_ms,
            limit_ip=3,  # fallback; в БД limit_ip часто 0, берём стандарт
            enable=True,
            verify_ssl=verify_ssl,
        )
        print(f"  Панель update: {'OK' if updated else 'FAILED'}")


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

    # По умолчанию dry-run=True — нужно явно передать --apply
    dry_run = "--apply" not in [a.lower() for a in os.sys.argv[1:]]

    print(f"Панель: {xui_api_url}")
    print(f"API base: {_api_base_url(xui_api_url)}")
    print(f"SSL verify: {not xui_skip_ssl}")
    print(f"Auth: {'Bearer token' if xui_token else 'login+cookie'}")
    print(f"Режим: {'DRY-RUN (никаких изменений)' if dry_run else 'APPLY'}")
    print()

    pool = await asyncpg.create_pool(database_url)
    try:
        targets = await _build_targets(pool)
        print(f"Найдено ключей для исправления: {len(targets)}")
        if not targets:
            return

        await _apply_fixes(
            pool=pool,
            targets=targets,
            base_url=xui_api_url,
            username=xui_login,
            password=xui_password,
            token=xui_token,
            verify_ssl=not xui_skip_ssl,
            dry_run=dry_run,
        )
    finally:
        await pool.close()

    if dry_run:
        print("\nДля применения изменений запусти:")
        print("  .venv/bin/python tools/fix_paid_keys_1970.py --apply")


if __name__ == "__main__":
    asyncio.run(main())
