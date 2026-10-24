"""Список ключей кабинета (веб и Mini App) отдаёт ссылку «Открыть в Happ»."""
from tests.integration.helpers import issue_trial

WEB_HEADERS = {"X-Requested-With": "web"}


def _session(tg_id: int) -> dict:
    from app.core.miniapp_session import web_sessions

    token, _ = web_sessions.issue(tg_id)
    return {**WEB_HEADERS, "Cookie": f"web_session={token}"}


async def test_cabinet_keys_have_happ_deep_link_to_subscription(backend):
    key = await issue_trial(backend, 7101)

    resp = await backend.client.get("/api/v1/web/keys", headers=_session(7101))

    assert resp.status_code == 200, resp.text
    [listed] = resp.json()
    assert listed["email"] == key["email"]
    # Happ получает subscription URL ключа как есть, без кодирования.
    assert listed["deep_link_happ"].startswith("happ://")
    assert listed["deep_link_happ"].endswith(listed["key"])
