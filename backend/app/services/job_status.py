"""Record of scheduled job outcomes, surfaced by /health.

Kept in memory for the process that runs the jobs (the scheduler leader) and
mirrored to the job_runs table, so the status survives restarts and is visible
from every replica (non-leaders run no jobs and have nothing in memory).
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_status: dict[str, dict[str, Any]] = {}


def record(job: str, ok: bool, error: str | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        entry = _status.setdefault(
            job, {"last_success": None, "last_failure": None, "last_error": None, "consecutive_failures": 0}
        )
        if ok:
            entry["last_success"] = now
            entry["consecutive_failures"] = 0
        else:
            entry["last_failure"] = now
            entry["last_error"] = (error or "")[:300]
            entry["consecutive_failures"] += 1
        return dict(entry)


def persist(job: str) -> None:
    """Best-effort upsert of the in-memory entry into job_runs (blocking)."""
    with _lock:
        entry = dict(_status.get(job) or {})
    if not entry:
        return
    try:
        from backend.app.db.supabase_client import get_supabase_client
        get_supabase_client().table("job_runs").upsert(
            {"job": job, **entry, "updated_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="job",
        ).execute()
    except Exception as e:
        logger.warning("Could not persist job status for %s: %s", job, e)


def snapshot() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _status.items()}


def snapshot_all() -> dict[str, dict[str, Any]]:
    """Persisted status overlaid with this process's (fresher) in-memory one."""
    merged: dict[str, dict[str, Any]] = {}
    try:
        from backend.app.db.supabase_client import get_supabase_client
        rows = get_supabase_client().table("job_runs").select(
            "job, last_success, last_failure, last_error, consecutive_failures").execute().data or []
        for row in rows:
            job = row.pop("job")
            merged[job] = row
    except Exception as e:
        logger.warning("Could not read persisted job status: %s", e)
    merged.update(snapshot())
    return merged
