"""Реестр фоновых задач синхронизации inbound-набора клиентов."""
import asyncio

import pytest

from services.core.panel.sync_jobs import InboundSyncJobs


async def _wait_done(jobs: InboundSyncJobs, job_id: str, timeout: float = 5.0) -> None:
    await asyncio.wait_for(jobs.wait(job_id), timeout=timeout)
    # Дать финальному finally дописать состояние.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_all_clients_processed():
    seen: list[tuple[str, list[int]]] = []

    async def sync(xui, email, target, *, verify=True):
        seen.append((email, list(target)))

    jobs = InboundSyncJobs(sync_one=sync)
    job_id, running = jobs.start(["a@x", "b@x", "c@x"], [11, 12])

    assert isinstance(job_id, str)
    assert running is None

    await _wait_done(jobs, job_id)
    state = await jobs.get(job_id)
    assert state.status == "done"
    assert state.processed == 3
    assert state.success == 3
    assert state.failed == 0
    assert state.finished_at is not None
    assert {email for email, _ in seen} == {"a@x", "b@x", "c@x"}
    assert all(target == [11, 12] for _, target in seen)


@pytest.mark.asyncio
async def test_errors_are_counted_and_capped():
    async def sync(xui, email, target, *, verify=True):
        raise RuntimeError(f"boom {email}")

    jobs = InboundSyncJobs(sync_one=sync)
    emails = [f"u{i}@x" for i in range(30)]
    job_id, _ = jobs.start(emails, [11])
    await _wait_done(jobs, job_id)

    state = await jobs.get(job_id)
    assert state.status == "done"
    assert state.processed == 30
    assert state.failed == 30
    assert len(state.errors) == 20
    assert {"email", "error"} <= set(state.errors[0])


@pytest.mark.asyncio
async def test_only_one_job_at_a_time():
    started = asyncio.Event()
    release = asyncio.Event()

    async def sync(xui, email, target, *, verify=True):
        started.set()
        await release.wait()

    jobs = InboundSyncJobs(sync_one=sync)
    first, running = jobs.start(["a@x"], [11])
    assert running is None
    await started.wait()

    second, running = jobs.start(["b@x"], [12])
    assert second is None
    assert running == first

    release.set()
    await _wait_done(jobs, first)
    assert (await jobs.get(first)).status == "done"

    third, running = jobs.start(["b@x"], [12])
    assert running is None and isinstance(third, str)
    release.set()
    await _wait_done(jobs, third)


@pytest.mark.asyncio
async def test_cancel_stops_job_and_reports_cancelled():
    release = asyncio.Event()

    async def sync(xui, email, target, *, verify=True):
        await release.wait()

    jobs = InboundSyncJobs(sync_one=sync)
    emails = [f"u{i}@x" for i in range(20)]
    job_id, _ = jobs.start(emails, [11])
    await asyncio.sleep(0)

    state = await jobs.cancel(job_id)
    assert state.cancel_requested is True
    release.set()
    await _wait_done(jobs, job_id)

    state = await jobs.get(job_id)
    assert state.status == "cancelled"
    assert state.processed < 20
    # Завершённую задачу отменить нельзя (API отвечает 409).
    await jobs.cancel(job_id)
    assert state.status == "cancelled"


@pytest.mark.asyncio
async def test_prune_keeps_only_last_finished_jobs():
    async def sync(xui, email, target, *, verify=True):
        return None

    jobs = InboundSyncJobs(sync_one=sync)
    ids = []
    for _ in range(23):
        job_id, _ = jobs.start(["a@x"], [11])
        await _wait_done(jobs, job_id)
        ids.append(job_id)

    # Самая старая задача вычищена, свежие доступны.
    assert await jobs.get(ids[0]) is None
    assert await jobs.get(ids[-1]) is not None


@pytest.mark.asyncio
async def test_target_snapshot_survives_changes():
    seen: list[list[int]] = []

    async def sync(xui, email, target, *, verify=True):
        seen.append(list(target))

    target = [11, 12]
    jobs = InboundSyncJobs(sync_one=sync)
    job_id, _ = jobs.start(["a@x"], target)
    target.append(13)  # внешняя мутация не должна влиять на задачу
    await _wait_done(jobs, job_id)

    assert seen == [[11, 12]]
    assert (await jobs.get(job_id)).as_dict()["target_inbound_ids"] == [11, 12]
