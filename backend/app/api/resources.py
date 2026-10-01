import logging
import threading
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from backend.app.auth import require_operator
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


@router.post("/discover", dependencies=[rate_limit("discover", 2)])
def trigger_discovery(user: dict = Depends(require_operator)):
    if not _discovery_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Discovery is already running")
    try:
        result = DiscoveryService().run()
    except DiscoveryError as e:
        # Partial success: some resource types failed at the provider. Which
        # ones goes to the log, not to the client.
        logger.warning("Manual discovery partially failed: %s", e.summary)
        raise HTTPException(status_code=502, detail="Discovery partially failed")
    except Exception:
        logger.exception("Manual discovery failed")
        raise HTTPException(status_code=500, detail="Discovery failed")
    finally:
        _discovery_lock.release()
    return {"status": "success", "result": result}
