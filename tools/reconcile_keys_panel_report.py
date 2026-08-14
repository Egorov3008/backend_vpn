"""DRY-RUN отчёт: сверка Keys(БД) vs Clients(панель), приоритет БД.

Не пишет НИЧЕГО ни в БД, ни в панель. Три секции отчёта:

  1. GRACE-кандидаты — ключи, которым по бизнес-правилу (paid/trial подписка,
     expiry_time уже прошёл, но ещё не прошло GRACE_PERIOD_DAYS) положен
     grace_expiry, а в БД он либо NULL, либо некорректен (<= expiry_time).
     Целевое значение: grace_expiry = expiry_time + GRACE_PERIOD_MS.

  2. INBOUND-расхождения — для ключей, не истёкших по DB-статусу
     (active ИЛИ grace — включая grace-кандидатов из п.1, как если бы grace
     уже был проставлен), сравнивает текущий набор inbound клиента в панели
     с целевым (по типу тарифа из БД + AVAILABLE_CONNECTIONS/XUI_INBOUND_ID_LANDING
     из .env).

  3. EXPIRY-расхождения — любое несовпадение expiry_time(БД) и expiryTime(панель)
     для сматченных по email клиентов. Только отчёт, ничего не чинится
     автоматически — решение за оператором.

DB-статус (active/grace/expired) считается ТОЛЬКО по полям БД
(keys.expiry_time / keys.grace_expiry), как в
backend/services/core/keys/utils/status.py::KeyStatus.of() — панель
как источник статуса не используется.

Использование:
  python3 backend/tools/reconcile_keys_panel_report.py                     # dry-run отчёт
  python3 backend/tools/reconcile_keys_panel_report.py --apply-grace       # + пишет grace_expiry в БД (п.1)
  python3 backend/tools/reconcile_keys_panel_report.py --apply-inbounds    # + attach недостающих inbound в панели (п.2, только missing, без detach)
  (флаги можно комбинировать; expiry-расхождения (п.3) и orphan-ключи без клиента в панели НИКОГДА не трогаются автоматически)
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

    async def list_clients(self):
        r = await self._req("GET", "/api/clients/list")
        r.raise_for_status()
        return r.json()

    async def attach(self, email, inbound_ids):
        r = await self._req("POST", f"/api/clients/{email}/attach",
                            json={"inboundIds": inbound_ids})
        r.raise_for_status()
        return r.json()

    async def detach(self, email, inbound_ids):
        r = await self._req("POST", f"/api/clients/{email}/detach",
                            json={"inboundIds": inbound_ids})
        r.raise_for_status()
        return r.json()


def classify(tariff_id, amount, trial_id) -> str:
    if tariff_id is None and amount is None:
        return "orphan"
    if (amount is not None and amount > 0) or (tariff_id == trial_id):
        return "subscription"
    return "free"


def db_status(expiry_time, grace_expiry, now_ms) -> str:
    if not expiry_time:
        return "none"
    if now_ms < expiry_time:
        return "active"
    grace = grace_expiry or 0
    if now_ms < grace:
        return "grace"
    return "expired"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply-grace", action="store_true")
    ap.add_argument("--apply-inbounds", action="store_true")
    args = ap.parse_args()

    env = load_env(ENV_PATH)

    landing = int(env.get("XUI_INBOUND_ID_LANDING", "0") or 0)
    overlay = parse_list(env.get("AVAILABLE_CONNECTIONS", "[]"))
    grace_days = int(env.get("GRACE_PERIOD_DAYS", "7") or 0)
    grace_ms = grace_days * 86_400_000
    trial_id = int(env.get("DEFAULT_PRICING_PLAN", "0") or 0)

    sub_target = ([landing] if landing else []) + [i for i in overlay if i != landing]
    free_target = list(overlay)
    grace_target = [landing] if landing else []

    now_ms = int(time.time() * 1000)

    print(f"=== now: {now_ms}  GRACE_PERIOD_DAYS={grace_days}  trial_id={trial_id}")
    print(f"=== subscription target: {sub_target}")
    print(f"=== free target        : {free_target}")
    print(f"=== grace target       : {grace_target}")
    print()

    dsn = (f"postgresql://{env['DB_USER']}:{env['DB_PASSWORD']}"
           f"@127.0.0.1:5433/{env['DB_NAME']}")
    conn = await asyncpg.connect(dsn=dsn)
    rows = await conn.fetch(
        "SELECT k.email, k.tg_id, k.expiry_time, k.grace_expiry, k.tariff_id, "
        "t.amount, k.inbound_id "
        "FROM keys k LEFT JOIN tariff t ON t.id = k.tariff_id"
    )
    db = {r["email"]: r for r in rows}
    print(f"=== keys в БД: {len(db)}")

    panel = PanelClient(
        base_url=env["XUI_API_URL"],
        username=env.get("XUI_LOGIN", ""),
        password=env.get("XUI_PASSWORD", ""),
        token=env.get("XUI_TOKEN") or env.get("XUI_API_TOKEN"),
    )
    lst = await panel.list_clients()
    clients = lst.get("obj", []) if isinstance(lst, dict) else (lst or [])
    panel_by_email = {}
    for cl in clients:
        email = (cl.get("email") or "").strip()
        if email:
            panel_by_email[email] = cl
    print(f"=== clients в панели: {len(panel_by_email)}")
    print()

    # ------------------------------------------------------------------
    # 1. GRACE-кандидаты
    # ------------------------------------------------------------------
    grace_candidates = []
    for email, r in db.items():
        exp = r["expiry_time"]
        if not exp or now_ms < exp:
            continue  # ещё active
        ktype = classify(r["tariff_id"], float(r["amount"]) if r["amount"] is not None else None, trial_id)
        if ktype != "subscription":
            continue
        target_grace = exp + grace_ms
        cur_grace = r["grace_expiry"] or 0
        needs_grace = (cur_grace <= exp) and (now_ms < target_grace)
        if needs_grace:
            grace_candidates.append((email, r["tg_id"], exp, r["grace_expiry"], target_grace))

    print(f"=== [1] GRACE-кандидаты (нужно проставить grace_expiry): {len(grace_candidates)}")
    print(f"{'email':<26} {'tg_id':<12} {'expiry_time':<14} {'grace_expiry(now)':<18} {'grace_expiry(target)':<20}")
    print("-" * 95)
    for email, tg_id, exp, cur_grace, target_grace in grace_candidates:
        print(f"{email:<26} {tg_id!s:<12} {exp:<14} {str(cur_grace):<18} {target_grace:<20}")
    print()

    if args.apply_grace and grace_candidates:
        print(f"=== APPLY GRACE: обновляю grace_expiry для {len(grace_candidates)} ключей... ===")
        for email, tg_id, exp, cur_grace, target_grace in grace_candidates:
            await conn.execute(
                "UPDATE keys SET grace_expiry = $1 WHERE email = $2", target_grace, email
            )
            print(f"  OK   {email} grace_expiry -> {target_grace}")
        print()

    # эффективный DB-статус с учётом grace-кандидатов "как если бы уже применили"
    grace_candidate_emails = {e for e, *_ in grace_candidates}

    def effective_status(email, r):
        st = db_status(r["expiry_time"], r["grace_expiry"], now_ms)
        if st == "expired" and email in grace_candidate_emails:
            return "grace"
        return st

    # ------------------------------------------------------------------
    # 2. INBOUND-расхождения (для active/grace по DB-статусу)
    # ------------------------------------------------------------------
    inbound_rows = []
    for email, r in db.items():
        st = effective_status(email, r)
        if st not in ("active", "grace"):
            continue
        cl = panel_by_email.get(email)
        if cl is None:
            inbound_rows.append((email, st, "-", [], None, "NO_PANEL_CLIENT"))
            continue
        if not bool(cl.get("enable", True)):
            inbound_rows.append((email, st, "-", [], None, "DISABLED_IN_PANEL"))
            continue
        amt = float(r["amount"]) if r["amount"] is not None else None
        ktype = classify(r["tariff_id"], amt, trial_id)
        if st == "active":
            target = sub_target if ktype == "subscription" else free_target
        else:  # grace
            target = grace_target if ktype == "subscription" else []
        cur = sorted(int(x) for x in (cl.get("inboundIds") or []) if x is not None)
        missing = [i for i in target if i not in cur]
        extra = [i for i in cur if i not in target]
        if missing or extra:
            inbound_rows.append((email, st, ktype, cur, target, f"missing={missing} extra={extra}"))

    print(f"=== [2] INBOUND-расхождения (active/grace по DB-статусу): {len(inbound_rows)}")
    print(f"{'email':<26} {'status':<8} {'type':<13} {'current':<20} {'note'}")
    print("-" * 100)
    for email, st, ktype, cur, target, note in inbound_rows:
        print(f"{email:<26} {st:<8} {str(ktype):<13} {str(cur):<20} {note}")
    print()

    to_attach = []
    for email, st, ktype, cur, target, note in inbound_rows:
        if target is None:
            continue
        missing = [i for i in target if i not in cur]
        if missing:
            to_attach.append((email, missing))

    if args.apply_inbounds and to_attach:
        print(f"=== APPLY INBOUNDS: attach недостающих {len(to_attach)} клиентам (только missing, без detach)... ===")
        ok, fail = 0, 0
        for email, missing in to_attach:
            try:
                resp = await panel.attach(email, missing)
                if isinstance(resp, dict) and resp.get("success") is False:
                    print(f"  FAIL {email}: {resp.get('msg')!r}")
                    fail += 1
                else:
                    print(f"  OK   {email} +{missing}")
                    ok += 1
            except Exception as e:
                print(f"  ERR  {email}: {e!r}")
                fail += 1
        print(f"=== Готово: ok={ok} fail={fail} ===")
        print()

    # ------------------------------------------------------------------
    # 3. EXPIRY-расхождения между БД и панелью
    # ------------------------------------------------------------------
    # ВАЖНО (см. backend/services/core/keys/utils/grace.py::_apply_paid):
    # для подписок панель ПРЕДНАМЕРЕННО хранит expiryTime = grace_expiry
    # (пре-запись в момент продления), а не expiry_time. Это НЕ баг.
    # Поэтому "ожидаемое" значение панели = grace_expiry, если он задан,
    # иначе expiry_time.
    expiry_mismatches = []
    expected_preset_matches = 0
    for email, r in db.items():
        cl = panel_by_email.get(email)
        if cl is None:
            continue
        db_exp = r["expiry_time"] or 0
        expected = r["grace_expiry"] if r["grace_expiry"] else db_exp
        panel_exp = int(cl.get("expiryTime") or 0)
        if expected != panel_exp:
            diff_days = (panel_exp - expected) / 86_400_000
            expiry_mismatches.append((email, db_exp, r["grace_expiry"], panel_exp, diff_days))
        else:
            expected_preset_matches += 1

    print(f"=== [3] EXPIRY: панель совпадает с ожидаемым (expiry_time ИЛИ grace_expiry, если задан): {expected_preset_matches}")
    print(f"=== [3] EXPIRY реальные расхождения (панель != ни expiry_time, ни grace_expiry): {len(expiry_mismatches)}")
    print(f"{'email':<26} {'expiry_time(БД)':<16} {'grace_expiry(БД)':<18} {'expiryTime(панель)':<20} {'diff_days_vs_expected':<10}")
    print("-" * 100)
    for email, db_exp, gr_exp, panel_exp, diff_days in sorted(expiry_mismatches, key=lambda x: -abs(x[4])):
        print(f"{email:<26} {db_exp:<16} {str(gr_exp):<18} {panel_exp:<20} {diff_days:+.2f}")
    print()

    await conn.close()

    if not args.apply_grace and not args.apply_inbounds:
        print("=== DRY-RUN: ничего не записано ни в БД, ни в панель. Запустите с --apply-grace / --apply-inbounds. ===")
    else:
        print("=== Применены только запрошенные флагами секции. Expiry-расхождения и orphan-ключи НЕ трогались. ===")


if __name__ == "__main__":
    asyncio.run(main())
