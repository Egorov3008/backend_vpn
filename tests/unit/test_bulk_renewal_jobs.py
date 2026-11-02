"""Реестр фоновых задач массового продления: прогресс, ошибки, отмена, prune.

Проверяем поведение реестра на подменённой функции продления — без БД и панели.
"""
import asyncio

from services.core.keys.bulk_renewal import BulkRenewalJobs


def _fake_renew(record: list | None = None, fail: tuple[str, ...] = (), delay: float = 0.0):
    async def renew(pool, service_data, cache, xui, email, days):
        if delay:
            await asyncio.sleep(delay)
        if email.startswith("bad") or email in fail:
            raise RuntimeError(f"panel rejected {email}")
        if record is not None:
            record.append(email)
        return 0

    return renew


def _emails(count: int, prefix: str = "key") -> list[str]:
    return [f"{prefix}-{i}@example.test" for i in range(count)]


async def test_all_keys_are_processed():
    seen: list[str] = []
    jobs = BulkRenewalJobs(renew_one=_fake_renew(seen))

    job_id, existing = await jobs.start(_emails(3), 5, admin_tg_id=7)
    await jobs.wait(job_id)
    state = await jobs.get(job_id)

    assert existing is None
    assert state is not None
    assert state.status == "done"
    assert state.total_keys == 3
    assert state.processed == 3
    assert state.success == 3
    assert state.failed == 0
    assert state.errors == []
    assert sorted(seen) == _emails(3)
    assert state.finished_at is not None


async def test_failures_are_counted_and_errors_are_capped():
    jobs = BulkRenewalJobs(renew_one=_fake_renew())

    job_id, _ = await jobs.start(_emails(25, "bad"), 5, admin_tg_id=7)
    await jobs.wait(job_id)
    state = await jobs.get(job_id)

    assert state.status == "done"
    assert state.processed == 25
    assert state.success == 0
    assert state.failed == 25
    assert len(state.errors) == 20
    assert all("panel rejected" in e["error"] for e in state.errors)


async def test_second_start_is_refused_while_running():
    gate = asyncio.Event()
    started: list[str] = []

    async def renew(pool, service_data, cache, xui, email, days):
        started.append(email)
        await gate.wait()
        return 0

    jobs = BulkRenewalJobs(renew_one=renew)
    first_id, existing = await jobs.start(_emails(2), 5, admin_tg_id=7)
    assert existing is None

    second_id, existing_id = await jobs.start(_emails(2, "other"), 5, admin_tg_id=7)

    gate.set()
    await jobs.wait(first_id)

    assert second_id is None
    assert existing_id == first_id
    assert sorted(started) == _emails(2)


async def test_cancel_stops_remaining_keys():
    jobs = BulkRenewalJobs(renew_one=_fake_renew(delay=0.01))

    job_id, _ = await jobs.start(_emails(50), 5, admin_tg_id=7)
    state = await jobs.get(job_id)
    while state.processed == 0:
        await asyncio.sleep(0)

    status = await jobs.cancel(job_id)
    assert status is not None
    assert status.status == "running"

    await jobs.wait(job_id)
    state = await jobs.get(job_id)

    assert state.status == "cancelled"
    assert state.cancel_requested is True
    assert state.processed < state.total_keys


async def test_cancel_finished_job_reports_status():
    jobs = BulkRenewalJobs(renew_one=_fake_renew())

    job_id, _ = await jobs.start(_emails(1), 5, admin_tg_id=7)
    await jobs.wait(job_id)

    state = await jobs.cancel(job_id)

    assert state.status == "done"


async def test_get_unknown_job_returns_none():
    jobs = BulkRenewalJobs(renew_one=_fake_renew())

    assert await jobs.get("no-such-job") is None
    assert await jobs.cancel("no-such-job") is None


async def test_finished_jobs_are_pruned_to_twenty():
    jobs = BulkRenewalJobs(renew_one=_fake_renew())

    finished = []
    for _ in range(22):
        job_id, _ = await jobs.start(_emails(1), 5, admin_tg_id=7)
        await jobs.wait(job_id)
        finished.append(job_id)

    assert len(jobs._jobs) == 20
    assert finished[0] not in jobs._jobs
    assert finished[-1] in jobs._jobs
