import asyncio
from contextlib import asynccontextmanager

import pytest

from backend.app import main, scheduler
from backend.app.config import settings
from backend.app.db import asyncpg_pool, schema
from backend.app.services import job_status


# -- DATABASE_URL validation -----------------------------------------------------

@pytest.mark.parametrize("url,pooled", [
    ("postgresql://u:p@aws-0-x.pooler.supabase.com:6543/postgres", True),
    ("postgresql://u:p@host:5432/postgres?pgbouncer=true", True),
    ("postgresql://u:p@aws-0-x.pooler.supabase.com:5432/postgres", False),
    ("postgresql://u:p@db.x.supabase.co:5432/postgres", False),
    ("postgresql://u:p@host/postgres", False),
])
def test_transaction_pooler_detection(url, pooled):
    assert asyncpg_pool.is_transaction_pooler(url) is pooled


def test_startup_refuses_transaction_pooler(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@h.pooler.supabase.com:6543/postgres")
    with pytest.raises(RuntimeError, match="transaction pooler"):
        main.validate_startup_config()


def test_startup_accepts_session_pooler(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@h.pooler.supabase.com:5432/postgres")
    main.validate_startup_config()


def test_pool_init_is_fatal_when_required(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    with pytest.raises(RuntimeError):
        asyncio.run(asyncpg_pool.init_db_pool(required=True))
    asyncio.run(asyncpg_pool.init_db_pool(required=False))  # tests/dev: warn only


def test_pool_connect_failure_is_fatal_and_hides_dsn(monkeypatch, caplog):
    async def boom(*a, **k):
        raise OSError("could not connect to postgresql://u:SECRETPW@h:5432/db")

    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:SECRETPW@h:5432/db")
    monkeypatch.setattr(asyncpg_pool.asyncpg, "create_pool", boom)
    with pytest.raises(RuntimeError, match="Could not connect"):
        asyncio.run(asyncpg_pool.init_db_pool(required=True))
    assert "SECRETPW" not in caplog.text


# -- schema version ----------------------------------------------------------------

class _Conn:
    def __init__(self, exists, version):
        self.exists, self.version = exists, version

    async def fetchval(self, sql, *args):
        return self.exists if "to_regclass" in sql else self.version


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        @asynccontextmanager
        async def ctx():
            yield conn
        return ctx()


def test_required_version_is_newest_migration_file():
    assert schema.latest_migration_version() >= 11


def _check(monkeypatch, exists, version):
    monkeypatch.setattr(schema, "get_pool", lambda: _Pool(_Conn(exists, version)))
    return asyncio.run(schema.check_schema_version())


def test_schema_check_passes_when_current(monkeypatch):
    latest = schema.latest_migration_version()
    assert _check(monkeypatch, True, latest) == latest


def test_schema_check_fails_when_behind(monkeypatch):
    with pytest.raises(schema.SchemaOutOfDate, match="apply the missing migrations"):
        _check(monkeypatch, True, schema.latest_migration_version() - 1)


def test_schema_check_fails_without_version_table(monkeypatch):
    with pytest.raises(schema.SchemaOutOfDate, match="schema_migrations table missing"):
        _check(monkeypatch, False, None)


# -- job status persistence ----------------------------------------------------------

@pytest.fixture
def clean_jobs():
    job_status._status.clear()
    yield
    job_status._status.clear()


def test_job_status_is_persisted_and_visible_to_other_replicas(fake_db, clean_jobs):
    job_status.record("discovery", False, "boom")
    job_status.persist("discovery")
    row = fake_db.rows("job_runs")[0]
    assert row["job"] == "discovery" and row["consecutive_failures"] == 1
    job_status._status.clear()  # a different replica: nothing in memory
    assert job_status.snapshot_all()["discovery"]["consecutive_failures"] == 1


def test_in_memory_status_wins_over_persisted(fake_db, clean_jobs):
    fake_db.rows("job_runs").append({"job": "discovery", "consecutive_failures": 4, "last_success": None,
                                     "last_failure": None, "last_error": "old"})
    job_status.record("discovery", True)
    assert job_status.snapshot_all()["discovery"]["consecutive_failures"] == 0


def test_job_status_persist_failure_is_not_fatal(fake_db, clean_jobs):
    fake_db.fail_tables["job_runs"] = True
    job_status.record("discovery", True)
    job_status.persist("discovery")
    assert "discovery" in job_status.snapshot_all()


# -- scheduler cadence -----------------------------------------------------------------

def test_job_intervals_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "JOB_DISCOVERY_MINUTES", 42)
    monkeypatch.setattr(settings, "JOB_PIPELINE_MINUTES", 7)
    try:
        scheduler.setup_scheduler()
        assert scheduler.scheduler.get_job("discovery_job").trigger.interval.total_seconds() == 42 * 60
        assert scheduler.scheduler.get_job("pipeline_job").trigger.interval.total_seconds() == 7 * 60
        # Work jobs start paused until leadership is acquired.
        assert scheduler.scheduler.get_job("discovery_job").next_run_time is None
    finally:
        scheduler.scheduler.remove_all_jobs()
