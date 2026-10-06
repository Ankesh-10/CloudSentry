import logging
import threading
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from backend.app.auth import actor_label, require_operator
from backend.app.config import settings
from backend.app.db.asyncpg_pool import get_pool
from backend.app.db.pagination import set_total
from backend.app.db.supabase_client import get_supabase_client
from backend.app.rate_limit import rate_limit
from backend.app.schemas.models import Resource
from backend.app.services import two_person
from backend.app.services.audit_logger import AuditLogger, AuditWriteError
from backend.app.services.discovery import DiscoveryError, DiscoveryService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[Resource])
def list_resources(
    response: Response,
    resource_type: Optional[str] = Query(default=None, max_length=30),
    include_deleted: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    query = get_supabase_client().table("resources").select("*", count="exact")
    if resource_type:
        query = query.eq("resource_type", resource_type)
    if not include_deleted:
        query = query.not_.in_("state", ["terminated", "deleted"])
    res = query.order("name").range(offset, offset + limit - 1).execute()
    set_total(response, res)
    return res.data


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


class ProtectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protected: bool


# Protecting is immediate (the safe direction). Unprotecting lets the agent act
# on the resource again, so it needs a second operator when
# REQUIRE_TWO_PERSON_CONFIG is on. The cloudsentry:protected tag is separate:
# a tagged resource stays protected whatever this flag says.
@router.patch("/{resource_id}/protection", response_model=Resource, dependencies=[rate_limit("protection", 20)])
def set_protection(resource_id: UUID, payload: ProtectionUpdate, user: dict = Depends(require_operator)):
    rid, actor, value = str(resource_id), actor_label(user), payload.protected
    db = get_supabase_client()
    res = db.table("resources").select("id, provider_id, protected").eq("id", rid).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Resource not found")
    current = res.data[0]
    params = {"from": bool(current.get("protected")), "to": value, "provider_id": current.get("provider_id")}

    def audit(event: str, message: str, detail: str) -> None:
        try:
            AuditLogger().log_action(event_type=event, actor=actor, resource_id=rid, request_params=params,
                                     message=message, required=True)
        except AuditWriteError:
            raise HTTPException(status_code=503, detail=f"Audit log unavailable; {detail}")

    pending_key = None
    if settings.REQUIRE_TWO_PERSON_CONFIG and value is False and current.get("protected") is True:
        pending_key = f"RESOURCE:{rid}:protected"
        pending = two_person.live_request(pending_key, value)
        if pending is None:
            audit("resource_protection_change_requested",
                  f"Unprotect {current.get('provider_id')} requested; awaiting a second operator",
                  "change not requested")
            expires_at = two_person.record_request(pending_key, value, actor)
            return JSONResponse(status_code=202, content={"status": "pending_confirmation", "resource_id": rid,
                                                          "protected": value, "expires_at": expires_at})
        if pending.get("requested_by") == actor:
            raise HTTPException(status_code=409, detail="A different operator must confirm this change")
        params["requested_by"] = pending.get("requested_by")

    audit("resource_protection_changed", f"{current.get('provider_id')} protected={value}", "change not applied")
    updated = db.table("resources").update({"protected": value}).eq("id", rid).execute()
    if pending_key:
        two_person.clear(pending_key)
    return updated.data[0]


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
