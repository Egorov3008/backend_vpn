"""Ретроактивная синхронизация панели для ВСЕХ клиентов 3x-ui.

Код с коммита 2242e34 проставляет EXTERNAL_SUB_URL и отсекает landing-inbound
только для НОВЫХ/продлеваемых ключей. Этот скрипт догоняет уже существующих
клиентов панели — источник списка ПАНЕЛЬ (GET /api/clients/list), не БД:
берутся вообще все клиенты 3x-ui, независимо от того, есть ли они в keys
и какой у них тариф (в т.ч. trial/landing/тестовые/MVP shared key).

1. Проставляет декоративную подписку в панели: externalLinks(kind=subscription)
   = EXTERNAL_SUB_URL (та же механика, что set_external_subscription
   в client.py — НЕ рабочая ссылка подписки, та остаётся на XUI_SUB).
2. Отвязывает inbound XUI_INBOUND_ID_LANDING (7) от клиента — НО пропускает
   клиентов, у которых этот inbound единственный (иначе клиент теряет доступ
   к VPN совсем); такие логируются отдельно для ручной проверки, inboundIds
   не трогаются.

Запускать внутри backend-контейнера (нужен /app/client.py и доступ к сети
docker-compose до panel):

    docker-compose exec backend python tools/sync_paid_keys_sub_and_drop_landing.py           # dry-run
    docker-compose exec backend python tools/sync_paid_keys_sub_and_drop_landing.py --apply    # применить

По умолчанию dry-run — ничего не пишет в панель, только печатает план.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

from client import _StandaloneClientAPI  # noqa: E402


async def fetch_all_panel_clients(api: _StandaloneClientAPI) -> list[dict]:
    """Возвращает сырые записи клиентов из GET /api/clients/list."""
    raw = await api.list()
    data = raw.get("obj", []) if isinstance(raw, dict) else raw
    clients = []
    for item in data or []:
        email = (item.get("email") or "").strip()
        if not email:
            continue
        clients.append(item)
    return clients


async def process_client(
    api: _StandaloneClientAPI,
    client_raw: dict,
    external_sub_url: str,
    landing_inbound_id: int,
    dry_run: bool,
    skipped_landing_only: list[str],
) -> None:
    email = client_raw["email"]
    inbound_ids = [int(i) for i in (client_raw.get("inboundIds") or [])]

    sub_url = external_sub_url

    detach_landing = landing_inbound_id in inbound_ids and len(inbound_ids) > 1
    landing_only = landing_inbound_id in inbound_ids and len(inbound_ids) <= 1

    prefix = "[dry-run] " if dry_run else ""
    parts = [f"externalLinks.subscription = {sub_url}"]
    if detach_landing:
        parts.append(f"detach inbound {landing_inbound_id} (current={inbound_ids})")
    if landing_only:
        parts.append(
            f"[SKIP detach] inbound {landing_inbound_id} — единственный у клиента, не трогаю inboundIds"
        )
        skipped_landing_only.append(email)

    print(f"{prefix}{email}: " + "; ".join(parts))

    if dry_run:
        return

    await api.external_links(email, [{"kind": "subscription", "value": sub_url}])
    if detach_landing:
        await api.detach(email, [landing_inbound_id])


async def main() -> None:
    dry_run = "--apply" not in sys.argv[1:]

    base_url = os.environ["XUI_API_URL"]
    username = os.environ.get("XUI_LOGIN", "")
    password = os.environ.get("XUI_PASSWORD", "")
    token = os.environ.get("XUI_TOKEN") or os.environ.get("XUI_API_TOKEN")
    external_sub_url = os.environ["EXTERNAL_SUB_URL"]
    landing_inbound_id = int(os.environ.get("XUI_INBOUND_ID_LANDING", "0"))

    if not landing_inbound_id:
        raise RuntimeError("XUI_INBOUND_ID_LANDING не задан в .env")
    if not external_sub_url:
        raise RuntimeError("EXTERNAL_SUB_URL не задан в .env")

    print(f"Панель: {base_url}")
    print(f"EXTERNAL_SUB_URL: {external_sub_url}")
    print(f"Landing inbound (detach target): {landing_inbound_id}")
    print(f"Режим: {'DRY-RUN (изменений в панель не будет)' if dry_run else 'APPLY'}")
    print()

    api = _StandaloneClientAPI(base_url=base_url, username=username, password=password, token=token)
    skipped_landing_only: list[str] = []

    clients = await fetch_all_panel_clients(api)
    print(f"Клиентов найдено в панели: {len(clients)}")
    if not clients:
        return
    for client_raw in clients:
        await process_client(
            api, client_raw, external_sub_url, landing_inbound_id, dry_run, skipped_landing_only
        )

    if skipped_landing_only:
        print(f"\nПропущен detach (inbound {landing_inbound_id} — единственный) для {len(skipped_landing_only)} клиентов:")
        for e in skipped_landing_only:
            print(f"  - {e}")

    if dry_run:
        print("\nДля применения изменений запусти:")
        print("  docker-compose exec backend python tools/sync_paid_keys_sub_and_drop_landing.py --apply")


if __name__ == "__main__":
    asyncio.run(main())
