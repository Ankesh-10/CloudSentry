import logging
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool

from backend.app.db.asyncpg_pool import get_pool
from backend.app.services import job_status, runtime_config
from ml.inference import InferenceEngine

logger = logging.getLogger(__name__)

TELEMETRY_STALE_MINUTES = 30
JOB_FAILURE_ALERT = 3


async def collect_health(detailed: bool = False) -> dict:
    """status: ok | degraded (serving, but something needs attention) |
    unhealthy (database unreachable -> 503).

    The public probe (detailed=False) omits job error text and spend figures.
    """
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
        "jobs": job_status.snapshot(),
        "issues": issues,
    }

    db_ok = False
    try:
        pool = get_pool()
        start = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            latest = await conn.fetchval("SELECT MAX(time) FROM resource_metrics")
        body["db"] = {"status": "ok", "latency_ms": round((datetime.now(timezone.utc) - start).total_seconds() * 1000, 2)}
        db_ok = True
        if latest:
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            stale = (datetime.now(timezone.utc) - latest).total_seconds() / 60.0
            body["telemetry"] = {"last_collection": latest.isoformat(), "staleness_minutes": round(stale, 1)}
            if stale > TELEMETRY_STALE_MINUTES and scheduler.is_leader():
                issues.append("telemetry_stale")
    except Exception as e:
        logger.warning("Health DB probe failed: %s", e)
        issues.append("database_unreachable")

    engine = InferenceEngine()
    loaded = engine.available_models()
    body["ml_model"] = {
        "status": "ok" if loaded else "cold_start",
        "models": loaded,
        "mode": "isolation_forest" if loaded else "zscore_ewma_fallback",
    }

    for name, st in body["jobs"].items():
        if st.get("consecutive_failures", 0) >= JOB_FAILURE_ALERT:
            issues.append(f"job_failing:{name}")

    if db_ok:
        try:
            from backend.app.services.safety_layer import SafetyLayer
            over, reason = await run_in_threadpool(SafetyLayer().budget_status)
            body["budget"] = {"over_budget": over, "reason": reason or None}
            if over:
                issues.append("over_budget")
        except Exception as e:
            logger.warning("Health budget probe failed: %s", e)

    if not db_ok:
        body["status"] = "unhealthy"
    elif issues:
        body["status"] = "degraded"
    if not detailed:
        body.pop("budget", None)
        body["jobs"] = {k: {"consecutive_failures": v["consecutive_failures"]} for k, v in body["jobs"].items()}
    return body
