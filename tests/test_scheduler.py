"""Scheduler: leadership gating of work jobs, and job outcome tracking."""
import asyncio

import pytest

from backend.app import scheduler
from backend.app.services import alerts, job_status


@pytest.fixture
def jobs(monkeypatch):
    job_status._status.clear()
    monkeypatch.setitem(scheduler._state, "leader", False)
    scheduler.setup_scheduler()
    yield scheduler.scheduler
    scheduler.scheduler.remove_all_jobs()
    job_status._status.clear()


def test_work_jobs_start_paused(jobs):
    for job_id in scheduler.LEADER_ONLY_JOBS:
        assert jobs.get_job(job_id).next_run_time is None
    # Config refresh and leadership polling run on every replica.
    assert jobs.get_job("leadership_job") is not None and jobs.get_job("config_refresh_job") is not None


def test_leadership_change_toggles_work_jobs(jobs, monkeypatch):
    state = {"leader": True}

    async def acquire():
        return state["leader"]

    toggled = []

    class _Job:  # APScheduler Job objects are read-only; record calls instead
        def __init__(self, job_id):
            self.job_id = job_id

        def resume(self):
            toggled.append(("resume", self.job_id))

        def pause(self):
            toggled.append(("pause", self.job_id))

    monkeypatch.setattr(scheduler.leader, "try_acquire", acquire)
    monkeypatch.setattr(jobs, "get_job", lambda job_id: _Job(job_id))

    asyncio.run(scheduler.job_leadership())
    assert scheduler.is_leader() is True
    assert {j for op, j in toggled if op == "resume"} == set(scheduler.LEADER_ONLY_JOBS)

    toggled.clear()
    asyncio.run(scheduler.job_leadership())   # unchanged: no churn
    assert toggled == []

    state["leader"] = False
    asyncio.run(scheduler.job_leadership())
    assert scheduler.is_leader() is False
    assert {j for op, j in toggled if op == "pause"} == set(scheduler.LEADER_ONLY_JOBS)


def test_tracked_records_success_and_failure(fake_db, monkeypatch):
    job_status._status.clear()
    sent = []
    monkeypatch.setattr(alerts, "job_failing", lambda name, entry: sent.append((name, entry["consecutive_failures"])))

    async def ok():
        return 42

    async def boom():
        raise ValueError("nope")

    try:
        assert asyncio.run(scheduler._tracked("demo", ok)) == 42
        for _ in range(3):
            assert asyncio.run(scheduler._tracked("demo", boom)) is None
        st = job_status.snapshot()["demo"]
        assert st["consecutive_failures"] == 3 and "ValueError" in st["last_error"]
        assert sent[-1] == ("demo", 3)
        assert fake_db.rows("job_runs")[0]["consecutive_failures"] == 3   # persisted
        asyncio.run(scheduler._tracked("demo", ok))
        assert job_status.snapshot()["demo"]["consecutive_failures"] == 0
    finally:
        job_status._status.clear()
