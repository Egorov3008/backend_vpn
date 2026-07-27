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

# --- P1: real SyncScheduler — race + scheduled registration ---------------
import asyncio  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402
from background.scheduler import SyncScheduler  # noqa: E402


@pytest.mark.asyncio
async def test_start_job_second_call_returns_existing_while_running():
    """Race fix: два start_job подряд, пока sync_cache ещё не выставил
    _sync_in_progress. Второй видит running JobState первого → (None, existing),
    а не второй 202."""
    sched = SyncScheduler(MagicMock(), MagicMock())
    # run_sync_job замокан — не зовёт sync_cache/_set_job, JobState остаётся running
    # (имитация окна до sync_cache).
    sched.run_sync_job = AsyncMock()
    try:
        j1, existing1 = await sched.start_job()
        assert j1 is not None and existing1 is None
        # Флаг ещё не поднят — именно в этом окне раньше второй start_job проходил.
        assert sched._sync_in_progress is False

        j2, existing2 = await sched.start_job()
        assert j2 is None
        assert existing2 == j1
    finally:
        # Дать созданным task'ам (mocked no-op) доработать, чтобы не текли.
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_scheduled_sync_visible_to_start_job_with_real_job_id():
    """P1-3: пока бежит scheduled sync, start_job отдаёт настоящий job_id
    зарегистрированной джобы (не 'unknown'). Раньше scheduled sync_cache бежал
    без JobState → 409 с job_id='unknown' → GET /admin/sync/unknown → 404."""
    sched = SyncScheduler(MagicMock(), MagicMock())
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_sync_cache():
        started.set()
        await release.wait()
        return {"status": "success"}

    sched.sync_cache = fake_sync_cache
    task = asyncio.create_task(sched.run_scheduled_sync())
    try:
        await started.wait()
        # scheduled sync бежит, _sync_in_progress не выставлен (fake), но
        # running JobState зарегистрирована — start_job должен её найти.
        job_id, existing = await sched.start_job()
        assert job_id is None
        assert existing is not None
        assert existing != "unknown"
        js = await sched.get_job(existing)
        assert js is not None and js.status == "running"
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_scheduled_sync_registers_done_jobstate():
    """run_scheduled_sync по завершении отмечает JobState как done с результатом."""
    sched = SyncScheduler(MagicMock(), MagicMock())
    sched.sync_cache = AsyncMock(return_value={"status": "success"})
    await sched.run_scheduled_sync()
    jobs = list(sched._jobs.values())
    assert len(jobs) == 1
    assert jobs[0].status == "done"
    assert jobs[0].result == {"status": "success"}
