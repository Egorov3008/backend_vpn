"""Сверка/дополнение inbound-ов клиентов панели под набор из .env (DRY-RUN по умолчанию).

Модель (backend/services/core/keys/utils/inbounds.py + formtion.py):
  - XUI_INBOUND_ID_LANDING (7)        — baseline (telegram).
  - AVAILABLE_CONNECTIONS ([2,3,4,5]) — paid overlay (полный VPN).

Целевой набор inbound зависит от ТИПА ключа (тариф из БД, не из панели):
  - подписка  (amount>0  ИЛИ  tariff_id==trial=DEFAULT_PRICING_PLAN) → [LANDING]+AVAILABLE
  - бесплатный (amount==0, не trial, не landing)                     → AVAILABLE (без LANDING)
  - landing 24h (tariff_id==999)                                     → [LANDING]
  - нет в БД (orphan)                                                → НЕ ТРОГАТЬ (только флаг)

Активность (active/grace/expired) — из БД (expiry_time/grace_expiry), НЕ из
панели, как в backend/services/core/keys/utils/status.py::KeyStatus.of():
  - enable=False (панель)                          → disabled → ПРОПУСК
  - нет строки в БД (orphan)                        → ПРОПУСК (см. classify)
  - now < expiry_time                               → active → target по типу
  - expiry_time <= now < grace_expiry (grace_expiry not null) → grace → для подписки [LANDING], иначе ПРОПУСК
  - иначе                                           → expired → ПРОПУСК

  ВАЖНО: раньше этот скрипт брал активность из panel.expiryTime — но для
  ключей с активной grace-моделью panel.expiryTime ВСЕГДА равен grace_expiry
  (конец оплаты + GRACE_PERIOD_DAYS), а не концу оплаты (см. инвариант в
  backend/services/core/keys/utils/grace.py). Из-за этого ключ в статусе
  GRACE читался как "active", и --apply мог повторно прицепить платный
  оверлей ключу, который должен быть ограничен до baseline на время grace.
  Исправлено 2026-08-13 — статус теперь считается по БД, как в
  reconcile_keys_panel_report.py и в самом бэкенде.

Режим --apply: только ATTACH недостающих (без detach), по одному клиенту.

Использование:
  python3 backend/tools/reconcile_inbounds.py            # dry-run
  python3 backend/tools/reconcile_inbounds.py --apply     # применить attach
"""
import argparse
import asyncio
import os
import time

import asyncpg
import httpx

ENV_PATH = os.environ.get("ENV_PATH", "/home/admin/platform/.env")


def load_env(path: str) -> dict[str, str]:
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def parse_list(raw: str) -> list[int]:
    raw = (raw or "").strip().strip("[]").strip()
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


class PanelClient:
    """Минимальный standalone-клиент 3x-ui v3.2.0 (token-first, cookie fallback)."""

    def __init__(self, base_url, username, password, token=None):
        self.base = base_url.rstrip("/")
        if not self.base.endswith("/panel"):
            self.base += "/panel"
        self.username = username
        self.password = password
        self._token = token
        self._cookie = None
        self._client = httpx.AsyncClient(timeout=30.0, verify=False)

    async def _ensure_auth(self):
        if self._token or self._cookie:
            return
        csrf = await self._client.get(f"{self.base}/csrf-token",
                                      headers={"Accept": "application/json"})
        csrf.raise_for_status()
        csrf_token = csrf.json().get("obj") or csrf.json().get("csrfToken")
        for n in ("session", "3x-ui"):
            v = csrf.cookies.get(n)
            if v:
                self._cookie = v
                break
        login = await self._client.post(
            f"{self.base}/login",
            data={"username": self.username, "password": self.password},
            headers={"X-CSRF-Token": csrf_token},
        )
        login.raise_for_status()
        for n in ("session", "3x-ui"):
            v = login.cookies.get(n)
            if v:
                self._cookie = v
                break
        if not self._cookie:
            raise RuntimeError("no session cookie after login")

    async def _req(self, method, path, **kw):
        await self._ensure_auth()
        headers = kw.pop("headers", {})
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        elif self._cookie:
            headers["Cookie"] = f"session={self._cookie}"
        return await self._client.request(method, f"{self.base}{path}",
                                          headers=headers, **kw)

    async def list_inbounds(self):
        r = await self._req("GET", "/api/inbounds/list")
        r.raise_for_status()
        return r.json()

    async def list_clients(self):
        r = await self._req("GET", "/api/clients/list")
        r.raise_for_status()
        return r.json()

    async def attach(self, email, inbound_ids):
        r = await self._req("POST", f"/api/clients/{email}/attach",
                            json={"inboundIds": inbound_ids})
        r.raise_for_status()
        return r.json()


def classify(tariff_id, amount, trial_id, landing_id=999) -> tuple[str, list[int] | None]:
    """Возвращает (тип, target) по тарифу. target=None → не трогать."""
    if tariff_id is None and amount is None:
        return "orphan", None
    if tariff_id == landing_id:
        return "landing", None  # target проставится отдельно как [LANDING]
    if (amount is not None and amount > 0) or (tariff_id == trial_id):
        return "subscription", None
    return "free", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--env", default=ENV_PATH)
    args = ap.parse_args()

    env = load_env(args.env)
    for k, v in env.items():
        os.environ.setdefault(k, v)

    landing = int(env.get("XUI_INBOUND_ID_LANDING", "0") or 0)
    overlay = parse_list(env.get("AVAILABLE_CONNECTIONS", "[]"))
    grace_days = int(env.get("GRACE_PERIOD_DAYS", "7") or 0)
    # grace-окно больше не считается вручную (now - grace_ms) — берём
    # db_grace_expiry прямо из БД (см. классификацию ниже). grace_days
    # оставлен только для информационной печати ниже.
    trial_id = int(env.get("DEFAULT_PRICING_PLAN", "0") or 0)
    landing_tariff_id = 999

    sub_target = []
    for i in ([landing] if landing else []) + overlay:
        if i not in sub_target:
            sub_target.append(i)
    free_target = list(overlay)
    landing_target = [landing] if landing else []
    grace_target = [landing] if landing else []

    print(f"=== LANDING (baseline) : {landing if landing else '(нет)'}")
    print(f"=== AVAILABLE (overlay): {overlay}")
    print(f"=== GRACE_PERIOD_DAYS  : {grace_days}  (trial_id={trial_id})")
    print(f"=== subscription target: {sub_target}")
    print(f"=== free       target  : {free_target}")
    print(f"=== landing    target  : {landing_target}")
    print(f"=== grace (sub) target : {grace_target}")
    print()

    panel = PanelClient(
        base_url=env["XUI_API_URL"],
        username=env.get("XUI_LOGIN", ""),
        password=env.get("XUI_PASSWORD", ""),
        token=env.get("XUI_TOKEN") or env.get("XUI_API_TOKEN"),
    )

    async def run():
        # --- DB: email -> (tariff_id, amount) ---
        dsn = (f"postgresql://{env['DB_USER']}:{env['DB_PASSWORD']}"
               f"@127.0.0.1:5433/{env['DB_NAME']}")
        conn = await asyncpg.connect(dsn=dsn)
        rows = await conn.fetch(
            "SELECT k.email, k.tariff_id, t.amount, k.expiry_time, k.grace_expiry "
            "FROM keys k LEFT JOIN tariff t ON t.id = k.tariff_id"
        )
        await conn.close()
        db = {r["email"]: (r["tariff_id"],
                          float(r["amount"]) if r["amount"] is not None else None,
                          r["expiry_time"], r["grace_expiry"])
              for r in rows}
        print(f"=== DB rows (keys): {len(db)}")

        # --- panel inbounds ---
        inb = await panel.list_inbounds()
        inb_objs = inb.get("obj", []) if isinstance(inb, dict) else (inb or [])
        panel_inbound_ids = sorted(int(o.get("id")) for o in inb_objs if o.get("id") is not None)
        print(f"=== panel inbound IDs: {panel_inbound_ids}")
        all_target_ids = set(sub_target) | set(free_target) | set(landing_target)
        missing_in_panel = sorted(all_target_ids - set(panel_inbound_ids))
        if missing_in_panel:
            print(f"!!! ВНИМАНИЕ: target-IDs отсутствуют в панели: {missing_in_panel}")
        print()

        # --- panel clients ---
        lst = await panel.list_clients()
        clients = lst.get("obj", []) if isinstance(lst, dict) else (lst or [])
        now_ms = int(time.time() * 1000)

        rows_out = []
        to_attach: list[tuple[str, list[int], str, str]] = []
        counts = {"active_sub": 0, "active_free": 0, "active_landing": 0,
                  "active_orphan": 0, "grace": 0, "expired": 0, "disabled": 0}

        for cl in clients:
            email = (cl.get("email") or "").strip()
            if not email:
                continue
            enable = bool(cl.get("enable", True))
            cur = [int(x) for x in (cl.get("inboundIds") or []) if x is not None]

            tid, amt, db_expiry, db_grace_expiry = db.get(email, (None, None, None, None))
            ktype, _ = classify(tid, amt, trial_id, landing_tariff_id)

            # Активность считаем ИЗ БД (expiry_time/grace_expiry), не из
            # panel.expiryTime — см. докстринг модуля. panel.expiryTime для
            # подписки хранит grace_expiry, поэтому не годится как источник
            # активности: ключ в реальном статусе GRACE читался бы как
            # active и получил бы обратно платный оверлей.
            if not enable:
                status = "disabled"
                target = None
                counts["disabled"] += 1
            elif db_expiry is None:
                # Нет строки в БД (orphan) — не знаем статус, не трогаем.
                status = "active"  # печатается как active/orphan ниже
                target = None
                counts["active_orphan"] += 1
            elif now_ms < db_expiry:
                status = "active"
                if ktype == "subscription":
                    target = sub_target; counts["active_sub"] += 1
                elif ktype == "free":
                    target = free_target; counts["active_free"] += 1
                elif ktype == "landing":
                    target = landing_target; counts["active_landing"] += 1
                else:  # orphan (classify says no tariff data despite db row)
                    target = None; counts["active_orphan"] += 1
            elif db_grace_expiry is not None and now_ms < db_grace_expiry:
                # grace: только для подписки → baseline; остальное пропускаем
                if ktype == "subscription":
                    status = "grace"; target = grace_target; counts["grace"] += 1
                else:
                    status = "expired"; target = None; counts["expired"] += 1
            else:
                status = "expired"; target = None; counts["expired"] += 1

            if target is None:
                if status == "active" and counts.get("active_orphan") is not None:
                    pass
                rows_out.append((email, status, ktype if status == "active" else "-",
                                 cur, []))
                continue

            missing = [i for i in target if i not in cur]
            rows_out.append((email, status,
                             "sub" if status == "active" else (ktype if status == "active" else "grace"),
                             cur, missing))
            if status == "active":
                ktype_label = {"subscription": "sub", "free": "free",
                                "landing": "land"}.get(ktype, ktype)
            else:
                ktype_label = "grace"
            if missing:
                to_attach.append((email, missing, status, ktype_label))

        # --- печать ---
        print(f"{'email':<26} {'status':<7} {'type':<5} {'current':<22} {'need+':<16}")
        print("-" * 80)
        for email, status, typ, cur, missing in rows_out:
            if status in ("expired", "disabled"):
                continue
            flag = "OK" if not missing else "MISS"
            print(f"{email:<26} {status:<7} {str(typ):<5} {str(cur):<22} {str(missing):<16} {flag}")
        print("-" * 80)
        print(f"Итого клиентов в панели: {len(clients)}")
        print(f"  active: sub={counts['active_sub']} free={counts['active_free']} "
              f"landing={counts['active_landing']} orphan={counts['active_orphan']}")
        print(f"  grace={counts['grace']}  expired={counts['expired']}  disabled={counts['disabled']}")
        print(f"Клиентов с недостающими inbound (к attach): {len(to_attach)}")
        total_links = sum(len(m) for _, m, _, _ in to_attach)
        print(f"Всего inbound-привязок будет добавлено: {total_links}")

        # разбивка по типу
        by_type: dict[str, int] = {}
        by_type_links: dict[str, int] = {}
        for _, m, _, typ in to_attach:
            by_type[typ] = by_type.get(typ, 0) + 1
            by_type_links[typ] = by_type_links.get(typ, 0) + len(m)
        for typ in sorted(by_type):
            print(f"  {typ}: {by_type[typ]} клиентов, +{by_type_links[typ]} привязок")
        print()

        if not args.apply:
            print("=== DRY-RUN: изменения НЕ применяются. Запустите с --apply. ===")
            return
        if not to_attach:
            print("=== Нечего применять. ===")
            return

        print(f"=== APPLY: attach {len(to_attach)} клиентам... ===")
        ok, fail = 0, 0
        for email, missing, status, typ in to_attach:
            try:
                resp = await panel.attach(email, missing)
                if isinstance(resp, dict) and resp.get("success") is False:
                    print(f"  FAIL {email} [{typ}/{status}]: {resp.get('msg')!r}")
                    fail += 1
                else:
                    print(f"  OK   {email} [{typ}/{status}] +{missing}")
                    ok += 1
            except Exception as e:
                print(f"  ERR  {email}: {e!r}")
                fail += 1
        print(f"=== Готово: ok={ok} fail={fail} ===")

    asyncio.run(run())


if __name__ == "__main__":
    main()