import logging
import os
import time
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool

from backend.app.config import settings
from backend.app.db.asyncpg_pool import get_pool
from backend.app.services import job_status, runtime_config
from ml.inference import InferenceEngine

logger = logging.getLogger(__name__)

TELEMETRY_STALE_MINUTES = 30
JOB_FAILURE_ALERT = 3
# The public probe is unauthenticated: answer repeated hits from a short cache
# so they cannot turn into a stream of DB queries against a small pool.
PUBLIC_CACHE_SECONDS = 5.0
# budget_status pages a month of cost_records; never run it per request.
BUDGET_CACHE_SECONDS = 60.0

_public_cache: dict = {"body": None, "at": 0.0}
_budget_cache: dict = {"value": None, "at": 0.0}


def models_dir_writable(path: str) -> bool:
    """True if the retrainer can write models here (creating it if missing)."""
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return bool(probe) and os.path.isdir(probe) and os.access(probe, os.W_OK | os.X_OK)


def reset_caches() -> None:
    _public_cache.update(body=None, at=0.0)
    _budget_cache.update(value=None, at=0.0)


async def _probe_db(body: dict, issues: list[str], is_leader: bool) -> bool:
    try:
        pool = get_pool()
        start = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            latest = await conn.fetchval("SELECT MAX(time) FROM resource_metrics")
        body["db"] = {"status": "ok", "latency_ms": round((datetime.now(timezone.utc) - start).total_seconds() * 1000, 2)}
        if latest:
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            stale = (datetime.now(timezone.utc) - latest).total_seconds() / 60.0
            body["telemetry"] = {"last_collection": latest.isoformat(), "staleness_minutes": round(stale, 1)}
            if stale > TELEMETRY_STALE_MINUTES and is_leader:
                issues.append("telemetry_stale")
        return True
    except Exception as e:
        logger.warning("Health DB probe failed: %s", e)
        issues.append("database_unreachable")
        return False


async def _budget_status():
    now = time.monotonic()
    if _budget_cache["value"] is None or now - _budget_cache["at"] >= BUDGET_CACHE_SECONDS:
        from backend.app.services.safety_layer import SafetyLayer
        _budget_cache["value"] = await run_in_threadpool(SafetyLayer().budget_status)
        _budget_cache["at"] = now
    return _budget_cache["value"]


async def collect_health(detailed: bool = False) -> dict:
    """status: ok | degraded (serving, but something needs attention) |
    unhealthy (database unreachable -> 503).

    The public probe (detailed=False) only reports overall status and DB
    reachability: no job names, issue list, spend or model details, and no
    work beyond one cached DB round-trip.
    """
    if not detailed:
        return await _public_health()

    from backend.app import scheduler

    issues: list[str] = []
    body = {
        "status": "ok",
        "db": {"status": "error"},
        "ml_model": {},
        "telemetry": {"last_collection": None, "staleness_minutes": None},
        "automation": {
            "enabled": runtime_config.automation_enabled(),
            "dry_run": runtime_config.dry_run_mode(),
        },
        "scheduler": {"running": scheduler.scheduler.running, "leader": scheduler.is_leader()},
        "jobs": await run_in_threadpool(job_status.snapshot_all),
        "issues": issues,
    }

    db_ok = await _probe_db(body, issues, scheduler.is_leader())

    engine = InferenceEngine()
    loaded = engine.available_models()
    info = engine.model_info()
    writable = models_dir_writable(engine.models_dir)
    body["ml_model"] = {
        "status": "ok" if loaded else "cold_start",
        "models": loaded,
        "mode": "isolation_forest" if loaded else "zscore_ewma_fallback",
        "details": info,
        "writable": writable,
    }
    if not writable:
        # Every nightly retrain would fail (e.g. a root-owned mounted disk).
        issues.append("model_dir_not_writable")
    for rtype, meta in info.items():
        trained_at = meta.get("trained_at")
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(trained_at)).total_seconds() / 3600
        except (TypeError, ValueError):
            continue
        if age_h > settings.ML_MODEL_STALE_HOURS:
            issues.append(f"model_stale:{rtype}")

    for name, st in body["jobs"].items():
        if st.get("consecutive_failures", 0) >= JOB_FAILURE_ALERT:
            issues.append(f"job_failing:{name}")

    if db_ok:
        try:
            over, reason = await _budget_status()
            body["budget"] = {"over_budget": over, "reason": reason or None}
            if over:
                issues.append("over_budget")
        except Exception as e:
            logger.warning("Health budget probe failed: %s", e)

    if not db_ok:
        body["status"] = "unhealthy"
    elif issues:
        body["status"] = "degraded"
    return body


async def _public_health() -> dict:
    now = time.monotonic()
    cached = _public_cache["body"]
    if cached is not None and now - _public_cache["at"] < PUBLIC_CACHE_SECONDS:
        return dict(cached)
    from backend.app import scheduler
    probe: dict = {"db": {"status": "error"}}
    db_ok = await _probe_db(probe, [], scheduler.is_leader())
    body = {"status": "ok" if db_ok else "unhealthy", "db": {"status": probe["db"]["status"]}}
    _public_cache.update(body=body, at=now)
    return dict(body)
