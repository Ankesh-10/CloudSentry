import logging
import threading
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from backend.app.auth import actor_label, require_operator
from backend.app.db.asyncpg_pool import get_pool
from backend.app.db.supabase_client import get_supabase_client
from backend.app.rate_limit import rate_limit
from backend.app.schemas.models import Resource
from backend.app.services.discovery import DiscoveryError, DiscoveryService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[Resource])
def list_resources(
    resource_type: Optional[str] = Query(default=None, max_length=30),
    include_deleted: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    query = get_supabase_client().table("resources").select("*")
    if resource_type:
        query = query.eq("resource_type", resource_type)
    if not include_deleted:
        query = query.not_.in_("state", ["terminated", "deleted"])
    return query.order("name").range(offset, offset + limit - 1).execute().data


def _resource_bundle(rid: str) -> dict:
    db = get_supabase_client()
    res = db.table("resources").select("*").eq("id", rid).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Resource not found")
    anomalies = db.table("anomalies").select("*").eq("resource_id", rid).order("detected_at", desc=True).limit(20).execute()
    actions = db.table("optimization_actions").select("*").eq("resource_id", rid).order("created_at", desc=True).limit(20).execute()
    return {"resource": res.data[0], "anomalies": anomalies.data or [], "actions": actions.data or []}


@router.get("/{resource_id}")
async def get_resource(resource_id: UUID):
    rid = str(resource_id)
    bundle = await run_in_threadpool(_resource_bundle, rid)

    metrics_24h = None
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT time, metric_name, value, unit
                FROM resource_metrics
                WHERE resource_id = $1 AND time > NOW() - INTERVAL '24 hours'
                ORDER BY time DESC
                LIMIT 5000
                """,
                resource_id,
            )
        metrics_24h = [dict(r) for r in records]
    except RuntimeError:
        # Pool not initialised: report "unavailable" (null), not "no data" ([]).
        metrics_24h = None

    return {**bundle, "metrics_24h": metrics_24h}


# One manual discovery at a time per process: each run fans out into many
# provider API calls, and overlapping runs only multiply throttling and cost.
_discovery_lock = threading.Lock()
# Outcome of the latest manual run, polled via GET /discover/status. A large
# account takes minutes to list; holding the request open that long hits proxy
# timeouts (and the client retries, starting another run).
_discovery_state: dict = {"status": "idle", "started_at": None, "finished_at": None,
                          "started_by": None, "result": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_discovery() -> None:
    try:
        result = DiscoveryService().run()
        _discovery_state.update(status="success", result=result)
    except DiscoveryError as e:
        # Partial success: some resource types failed at the provider. Which
        # ones goes to the log, not to the client.
        logger.warning("Manual discovery partially failed: %s", e.summary)
        _discovery_state.update(status="partial_failure", result=None)
    except Exception:
        logger.exception("Manual discovery failed")
        _discovery_state.update(status="failed", result=None)
    finally:
        _discovery_state["finished_at"] = _now_iso()
        _discovery_lock.release()


@router.post("/discover", status_code=202, dependencies=[rate_limit("discover", 2)])
def trigger_discovery(background_tasks: BackgroundTasks, user: dict = Depends(require_operator)):
    if not _discovery_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Discovery is already running")
    _discovery_state.update(status="running", started_at=_now_iso(), finished_at=None,
                            started_by=actor_label(user), result=None)
    background_tasks.add_task(_run_discovery)
    return {"status": "started", "result": None}


@router.get("/discover/status")
def discovery_status():
    return dict(_discovery_state)
