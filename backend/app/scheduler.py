import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.app.db import leader
from backend.app.services import job_status, runtime_config

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60})
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cloudsentry-job")
_services: dict = {}
_state = {"leader": False}

LEADER_ONLY_JOBS = [
    "discovery_job",
    "pipeline_job",
    "action_execute_job",
    "action_verify_job",
    "cost_estimation_job",
    "ml_retraining_job",
    "metric_retention_job",
]


def _svc(name: str):
    """Services are built lazily (not at import) so a misconfigured adapter
    cannot take the whole API down on import."""
    if name not in _services:
        from backend.app.services.action_runner import ActionRunner
        from backend.app.services.anomaly_detector import AnomalyDetectorService
        from backend.app.services.cost_estimator import CostEstimationService
        from backend.app.services.discovery import DiscoveryService
        from backend.app.services.ml_retrainer import MLRetrainingService
        from backend.app.services.policy_engine import PolicyEngine
        from backend.app.services.retention import RetentionService
        from backend.app.services.telemetry import TelemetryService

        factories = {
            "discovery": DiscoveryService,
            "telemetry": TelemetryService,
            "cost": CostEstimationService,
            "detector": AnomalyDetectorService,
            "policy": PolicyEngine,
            "retrainer": MLRetrainingService,
            "runner": ActionRunner,
            "retention": RetentionService,
        }
        _services[name] = factories[name]()
    return _services[name]


async def _run_sync(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn, *args)


async def _tracked(name: str, coro_factory):
    try:
        result = await coro_factory()
    except Exception as e:
        logger.exception("Job %s failed", name)
        job_status.record(name, False, f"{type(e).__name__}: {e}")
        return None
    job_status.record(name, True)
    return result


async def job_discovery():
    await _tracked("discovery", lambda: _run_sync(_svc("discovery").run))


async def job_pipeline():
    """telemetry -> anomaly detection -> policy evaluation, in order, so
    detection always sees the samples this cycle just collected."""
    await _tracked("telemetry", lambda: _svc("telemetry").run())
    await _tracked("anomaly_detection", lambda: _svc("detector").run())
    await _tracked("policy_engine", lambda: _run_sync(_svc("policy").evaluate_all))


async def job_execute():
    await _tracked("action_execute", lambda: _run_sync(_svc("runner").execute_pending_auto))


async def job_verify():
    await _tracked("action_verify", lambda: _run_sync(_svc("runner").verify_pending))


async def job_cost():
    await _tracked("cost_estimation", lambda: _run_sync(_svc("cost").run))


async def job_retrain():
    await _tracked("ml_retraining", lambda: _svc("retrainer").run())


async def job_retention():
    await _tracked("metric_retention", lambda: _svc("retention").run())


async def job_refresh_config():
    await _run_sync(runtime_config.refresh_from_db)


async def job_leadership():
    """Acquire or confirm leadership; pause work jobs while not leader."""
    is_leader_now = await leader.try_acquire()
    if is_leader_now == _state["leader"]:
        return
    _state["leader"] = is_leader_now
    for job_id in LEADER_ONLY_JOBS:
        job = scheduler.get_job(job_id)
        if job is None:
            continue
        if is_leader_now:
            job.resume()
        else:
            job.pause()
    logger.warning("Scheduler leadership %s", "acquired" if is_leader_now else "lost; work jobs paused")


def is_leader() -> bool:
    return _state["leader"]


def setup_scheduler():
    logger.info("Setting up APScheduler jobs...")
    add = scheduler.add_job
    add(job_discovery, "interval", minutes=15, id="discovery_job", replace_existing=True)
    add(job_pipeline, "interval", minutes=5, id="pipeline_job", replace_existing=True)
    add(job_execute, "interval", minutes=2, id="action_execute_job", replace_existing=True)
    add(job_verify, "interval", minutes=2, id="action_verify_job", replace_existing=True)
    add(job_cost, "cron", minute=5, id="cost_estimation_job", replace_existing=True)
    add(job_retrain, "cron", hour=2, minute=0, id="ml_retraining_job", replace_existing=True, misfire_grace_time=3600)
    add(job_retention, "cron", hour=3, minute=0, id="metric_retention_job", replace_existing=True, misfire_grace_time=3600)
    add(job_refresh_config, "interval", minutes=1, id="config_refresh_job", replace_existing=True)
    add(job_leadership, "interval", seconds=30, id="leadership_job", replace_existing=True)
    # Work jobs start paused; job_leadership resumes them once the lock is held.
    for job_id in LEADER_ONLY_JOBS:
        scheduler.get_job(job_id).pause()
    logger.info("APScheduler jobs configured.")


async def start():
    setup_scheduler()
    scheduler.start()
    await job_leadership()


async def shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await leader.release()
