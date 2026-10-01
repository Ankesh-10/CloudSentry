from datetime import datetime, timezone
from functools import lru_cache
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.app.auth import actor_label, require_operator
from backend.app.db.supabase_client import get_supabase_client
from backend.app.rate_limit import rate_limit
from backend.app.schemas.models import Action
from backend.app.services.action_runner import REVERSIBLE, ActionRunner

router = APIRouter()

# Per-operator ceiling on state-changing calls (approve/execute/rollback share it).
_mutation_limit = [rate_limit("actions", 30)]

ActionStatus = Literal[
    "pending", "pending_approval", "approved", "rejected", "executing", "completed", "failed", "rolled_back"
]


@lru_cache(maxsize=1)
def get_runner() -> ActionRunner:
    return ActionRunner()


class ActionApproval(BaseModel):
    approved: bool


def _get(action_id: UUID) -> dict:
    res = get_supabase_client().table("optimization_actions").select("*").eq("id", str(action_id)).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Action not found")
    return res.data[0]


# Handlers are sync `def`: supabase-py is blocking, so FastAPI runs them in its
# threadpool instead of on the event loop.

@router.get("/", response_model=List[Action])
def list_actions(
    status: Optional[ActionStatus] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    query = get_supabase_client().table("optimization_actions").select("*")
    if status:
        query = query.eq("status", status)
    return query.order("created_at", desc=True).range(offset, offset + limit - 1).execute().data


@router.get("/{action_id}", response_model=Action)
def get_action(action_id: UUID):
    return _get(action_id)


@router.post("/{action_id}/approve", response_model=Action, dependencies=_mutation_limit)
def approve_action(
    action_id: UUID,
    payload: ActionApproval,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_operator),
):
    action = _get(action_id)
    if action["status"] not in ("pending", "pending_approval"):
        raise HTTPException(status_code=409, detail="Action is not awaiting approval")
    runner = get_runner()
    if payload.approved and runner.is_expired(action):
        runner.expire(action, actor_label(user))
        raise HTTPException(status_code=409, detail="Action proposal has expired; wait for it to be re-proposed")

    new_status = "approved" if payload.approved else "rejected"
    # Compare-and-swap on the status we read: a concurrent approve/execute wins once.
    res = (
        get_supabase_client().table("optimization_actions")
        .update({
            "status": new_status,
            "approved_by": actor_label(user),
            "approved_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", str(action_id))
        .eq("status", action["status"])
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=409, detail="Action changed concurrently; reload and retry")

    if payload.approved:
        background_tasks.add_task(get_runner().execute_action, str(action_id), actor_label(user))
    return res.data[0]


@router.post("/{action_id}/execute", status_code=202, dependencies=_mutation_limit)
def execute_action(action_id: UUID, background_tasks: BackgroundTasks, user: dict = Depends(require_operator)):
    action = _get(action_id)
    runnable = action["status"] == "approved" or (
        action["status"] == "pending" and not action.get("requires_approval")
    )
    if not runnable:
        raise HTTPException(status_code=409, detail="Action cannot be executed from its current status")
    background_tasks.add_task(get_runner().execute_action, str(action_id), actor_label(user))
    return {"status": "queued", "id": str(action_id)}


@router.post("/{action_id}/rollback", status_code=202, dependencies=_mutation_limit)
def rollback_action(action_id: UUID, background_tasks: BackgroundTasks, user: dict = Depends(require_operator)):
    action = _get(action_id)
    if action["status"] != "completed":
        raise HTTPException(status_code=409, detail="Can only roll back completed actions")
    if action.get("rollback_action_id"):
        raise HTTPException(status_code=409, detail="Action has already been rolled back")
    if action["action_type"] not in REVERSIBLE:
        raise HTTPException(status_code=409, detail="Action type is not reversible")
    background_tasks.add_task(get_runner().rollback_action, str(action_id), actor_label(user))
    return {"status": "queued", "id": str(action_id)}
