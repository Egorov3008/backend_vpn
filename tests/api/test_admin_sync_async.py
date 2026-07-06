"""Тесты async admin/sync: 202 + job_id, 409 при двойном запуске, GET статуса."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.main import app


class _FakeScheduler:
    """Фейк SyncScheduler: имитирует start_job/get_job без запуска реальной sync_cache.

    Первый вызов start_job возвращает новый job_id; второй (пока job running) — (None, existing).
    """

    def __init__(self):
        self._jobs: dict[str, SimpleNamespace] = {}
        self._counter = 0
        # Трекаем вызов run_sync_job, чтобы не запускать реальную синхронизацию.
        self.run_sync_job = AsyncMock()

    async def start_job(self):
        # Имитация логики реального start_job: если есть running — отказ.
        running = next(
            (j for j in self._jobs.values() if j.status == "running"), None
        )
        if running is not None:
            return None, running.job_id
        self._counter += 1
        job_id = f"job-{self._counter}"
        self._jobs[job_id] = SimpleNamespace(
            job_id=job_id, status="running", result=None, error=None
        )
        return job_id, None

    async def get_job(self, job_id):
        return self._jobs.get(job_id)

    def mark_done(self, job_id, status="done", result=None, error=None):
        js = self._jobs[job_id]
        js.status = status
        js.result = result
        js.error = error


@pytest.fixture
def fake_scheduler(monkeypatch):
    sched = _FakeScheduler()
    # Эмуляция app.state.sync_scheduler, выставляемого в lifespan.
    setattr(app.state, "sync_scheduler", sched)
    yield sched
    # Не удаляем атрибут — другие тесты могут полагаться на отсутствие/наличие;
    # но api_client teardown чистит dependency_overrides. Удалим, чтобы не течь.
    if hasattr(app.state, "sync_scheduler"):
        delattr(app.state, "sync_scheduler")


@pytest.mark.asyncio
async def test_sync_returns_202_with_job_id(api_client, fake_scheduler):
    r = await api_client.post("/api/v1/admin/sync")
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "running"
    assert body["job_id"]  # непустой


@pytest.mark.asyncio
async def test_sync_409_when_already_running(api_client, fake_scheduler):
    first = await api_client.post("/api/v1/admin/sync")
    assert first.status_code == 202
    existing_id = first.json()["job_id"]

    second = await api_client.post("/api/v1/admin/sync")
    assert second.status_code == 409
    body = second.json()
    # HTTPException detail оборачивается в {"detail": ...}.
    detail = body["detail"]
    assert detail["job_id"] == existing_id


@pytest.mark.asyncio
async def test_sync_status_unknown_job_404(api_client, fake_scheduler):
    r = await api_client.get("/api/v1/admin/sync/nonexistent-job")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_sync_status_running_then_done(api_client, fake_scheduler):
    start = await api_client.post("/api/v1/admin/sync")
    job_id = start.json()["job_id"]

    # Пока running — 200 со статусом running.
    r1 = await api_client.get(f"/api/v1/admin/sync/{job_id}")
    assert r1.status_code == 200
    assert r1.json()["status"] == "running"

    # Эмулируем завершение джобы.
    fake_scheduler.mark_done(job_id, status="done", result={"status": "success"})
    r2 = await api_client.get(f"/api/v1/admin/sync/{job_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "done"
    assert body["result"] == {"status": "success"}
    assert body["error"] is None


@pytest.mark.asyncio
async def test_sync_status_error_propagates(api_client, fake_scheduler):
    start = await api_client.post("/api/v1/admin/sync")
    job_id = start.json()["job_id"]
    fake_scheduler.mark_done(job_id, status="error", error="boom")
    r = await api_client.get(f"/api/v1/admin/sync/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["error"] == "boom"