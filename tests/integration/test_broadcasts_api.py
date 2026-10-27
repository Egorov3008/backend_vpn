"""Массовая рассылка из админки: кто получил сообщение, что записано в БД, что видит админ.

Telegram — заглушка e2e/fakes.py: проверяем, что реально ушло в чаты, а не вызовы.
"""
import pytest

from tests.integration.helpers import issue_trial, register_user

ADMIN_TG_ID = 9001


@pytest.fixture(autouse=True)
def _fast_broadcasts(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "broadcast_rate_per_second", 30.0)


def _admin_headers() -> dict:
    from config import settings

    return {"X-API-Key": settings.admin_api_key, "X-Admin-Tg-Id": str(ADMIN_TG_ID)}


def _received(backend, chat_id: int) -> list[dict]:
    return [m for m in backend.fakes.sent_messages(chat_id) if m["method"].lower().startswith("send")]


async def _users(backend, *tg_ids: int) -> None:
    for tg_id in (ADMIN_TG_ID, *tg_ids):
        await register_user(backend, tg_id)


async def _start(backend, **body) -> dict:
    resp = await backend.client.post("/api/v1/admin/broadcasts", json=body, headers=_admin_headers())
    assert resp.status_code == 202, resp.text
    return resp.json()


async def _finished(backend, broadcast_id: int) -> dict:
    from app.factories import broadcast_jobs

    await broadcast_jobs().wait(broadcast_id)
    resp = await backend.client.get(f"/api/v1/admin/broadcasts/{broadcast_id}", headers=_admin_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_broadcast_reaches_everyone_and_counts_blocked(backend):
    await _users(backend, 2001, 2002, 2003)
    backend.fakes.block_bot(2002)

    started = await _start(backend, text="<b>Новость</b> для всех", audience="all")
    done = await _finished(backend, started["id"])

    assert done["status"] == "done"
    assert done["progress"] == {"total": 3, "pending": 0, "sent": 2, "blocked": 1, "failed": 0}
    for tg_id in (2001, 2003):
        [msg] = _received(backend, tg_id)
        assert msg["params"]["text"] == "<b>Новость</b> для всех"
    assert _received(backend, 2002) == []
    # Админ получает одну контрольную копию и в общий список не входит
    assert len(_received(backend, ADMIN_TG_ID)) == 1


async def test_audience_with_active_key_only(backend):
    await _users(backend, 2102)
    await issue_trial(backend, 2101)
    backend.fakes.reset()

    resp = await backend.client.get("/api/v1/admin/broadcasts/audiences", headers=_admin_headers())
    counts = {a["id"]: a["count"] for a in resp.json()["audiences"]}
    assert counts["all"] == 2
    assert counts["active_key"] == 1
    assert counts["no_active_key"] == 1

    started = await _start(backend, text="Вашему ключу", audience="active_key")
    done = await _finished(backend, started["id"])

    assert done["progress"]["sent"] == 1
    assert len(_received(backend, 2101)) == 1
    assert _received(backend, 2102) == []


async def test_uploaded_video_goes_out_with_miniapp_button(backend, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "miniapp_url", "https://miniapp.example.test/app")
    await _users(backend, 2201)

    upload = await backend.client.post(
        "/api/v1/admin/broadcasts/media",
        files={"file": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42" * 100, "video/mp4")},
        headers=_admin_headers(),
    )
    assert upload.status_code == 200, upload.text
    media = upload.json()
    assert media["type"] == "video"

    started = await _start(
        backend,
        text="Смотрите видео",
        media={"type": media["type"], "file_id": media["file_id"], "file_name": media["file_name"]},
        button={"kind": "miniapp", "text": "Открыть приложение"},
        audience="all",
    )
    await _finished(backend, started["id"])

    [msg] = _received(backend, 2201)
    assert msg["method"] == "sendVideo"
    assert msg["params"]["video"] == media["file_id"]
    assert msg["params"]["caption"] == "Смотрите видео"
    [[button]] = msg["params"]["reply_markup"]["inline_keyboard"]
    assert button["web_app"]["url"] == "https://miniapp.example.test/app"


async def test_large_photo_is_sent_as_file(backend):
    from services.broadcasts.media import PHOTO_MAX_BYTES

    await _users(backend)
    upload = await backend.client.post(
        "/api/v1/admin/broadcasts/media",
        files={"file": ("big.jpg", b"0" * (PHOTO_MAX_BYTES + 1), "image/jpeg")},
        headers=_admin_headers(),
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["type"] == "document"


async def test_message_telegram_rejects_is_not_broadcast(backend):
    await _users(backend, 2301)

    resp = await backend.client.post(
        "/api/v1/admin/broadcasts", json={"text": "<b>не закрыт", "audience": "all"}, headers=_admin_headers()
    )

    assert resp.status_code == 422
    assert await backend.pool.fetchval("SELECT COUNT(*) FROM broadcasts") == 0
    assert _received(backend, 2301) == []


async def test_preview_needs_admin_chat_with_user_bot(backend):
    await _users(backend)
    backend.fakes.block_bot(ADMIN_TG_ID)

    resp = await backend.client.post(
        "/api/v1/admin/broadcasts/preview", json={"text": "Черновик"}, headers=_admin_headers()
    )

    assert resp.status_code == 409


async def test_preview_goes_only_to_admin(backend):
    await _users(backend, 2401)

    resp = await backend.client.post(
        "/api/v1/admin/broadcasts/preview", json={"text": "Черновик", "pin": True}, headers=_admin_headers()
    )

    assert resp.status_code == 204
    assert len(_received(backend, ADMIN_TG_ID)) == 1
    assert _received(backend, 2401) == []
    assert await backend.pool.fetchval("SELECT COUNT(*) FROM broadcasts") == 0


async def test_flood_limit_is_retried(backend):
    await _users(backend, 2501)
    backend.fakes.flood_once(2501, retry_after=1)

    started = await _start(backend, text="Повторим", audience="all")
    done = await _finished(backend, started["id"])

    assert done["progress"]["sent"] == 1
    assert len(_received(backend, 2501)) == 1


async def test_pinned_broadcast_is_pinned(backend):
    await _users(backend, 2601)

    started = await _start(backend, text="Важно", pin=True, audience="all")
    await _finished(backend, started["id"])

    methods = [m["method"].lower() for m in backend.fakes.sent_messages(2601)]
    assert methods == ["sendmessage", "pinchatmessage"]


async def test_only_one_broadcast_at_a_time_and_cancel(backend):
    from services.broadcasts.audiences import find_audience
    from services.broadcasts.models import BroadcastMessage
    from services.broadcasts.repository import BroadcastRepository

    await _users(backend, 2701, 2702)
    # Рассылка, у которой в этом процессе нет задачи (как после рестарта)
    broadcast_id = await BroadcastRepository(backend.pool).create(
        ADMIN_TG_ID, find_audience("all"), BroadcastMessage(text="Висит")
    )

    second = await backend.client.post(
        "/api/v1/admin/broadcasts", json={"text": "Ещё одна", "audience": "all"}, headers=_admin_headers()
    )
    assert second.status_code == 409

    resp = await backend.client.post(f"/api/v1/admin/broadcasts/{broadcast_id}/cancel", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == "canceled"
    assert resp.json()["progress"]["pending"] == 2
    assert _received(backend, 2701) == []


async def test_interrupted_broadcast_resumes_without_duplicates(backend):
    from app.factories import broadcast_jobs, build_broadcast_runner
    from services.broadcasts.audiences import find_audience
    from services.broadcasts.models import BroadcastMessage, DeliveryStatus
    from services.broadcasts.repository import BroadcastRepository

    await _users(backend, 2801, 2802, 2803)
    repo = BroadcastRepository(backend.pool)
    broadcast_id = await repo.create(ADMIN_TG_ID, find_audience("all"), BroadcastMessage(text="После рестарта"))
    # До остановки процесса: 2801 уже получил, 2802 был взят в отправку, 2803 ждёт
    await repo.claim(broadcast_id, 2)
    await repo.record(broadcast_id, 2801, DeliveryStatus.SENT, None)

    resumed = await broadcast_jobs().resume(repo, lambda: build_broadcast_runner(backend.pool))
    done = await _finished(backend, broadcast_id)

    assert resumed == [broadcast_id]
    assert done["status"] == "done"
    assert done["progress"]["sent"] == 3
    assert _received(backend, 2801) == []
    assert len(_received(backend, 2802)) == 1
    assert len(_received(backend, 2803)) == 1


async def test_history_lists_newest_first(backend):
    await _users(backend, 2901)
    first = await _start(backend, text="Первая", audience="all")
    await _finished(backend, first["id"])
    second = await _start(backend, text="Вторая", audience="all")
    await _finished(backend, second["id"])

    resp = await backend.client.get("/api/v1/admin/broadcasts", headers=_admin_headers())

    assert resp.headers["X-Total-Count"] == "2"
    assert [b["text"] for b in resp.json()["broadcasts"]] == ["Вторая", "Первая"]


async def test_bot_secret_alone_cannot_broadcast(backend):
    await _users(backend, 3001)

    resp = await backend.client.post("/api/v1/admin/broadcasts", json={"text": "Спам", "audience": "all"})

    assert resp.status_code in (401, 403)
    assert _received(backend, 3001) == []
