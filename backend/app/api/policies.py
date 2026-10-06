"""Read and toggle remediation policies.

Policies were only editable with SQL. Operators can now turn a policy on or
off and choose whether its actions wait for approval. Conditions, action type
and priority stay SQL-only: they change what the agent does, not just whether.

Like the runtime config, a change that lets the agent do more (enabling a
policy, or letting its actions run without approval) waits for a second
operator when REQUIRE_TWO_PERSON_CONFIG is on. Every change is audited first.
"""
import json
from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from backend.app.auth import actor_label, require_operator
from backend.app.config import settings
from backend.app.db.supabase_client import get_supabase_client
from backend.app.rate_limit import rate_limit
from backend.app.services.audit_logger import AuditLogger, AuditWriteError
from backend.app.services.runtime_config import PENDING_PREFIX, PENDING_TTL

router = APIRouter()

POLICY_PENDING_PREFIX = PENDING_PREFIX + "POLICY:"


class Policy(BaseModel):
    id: str
    name: Optional[str] = None
    enabled: Optional[bool] = None
    resource_type: Optional[str] = None
    anomaly_type: Optional[str] = None
    conditions: Optional[Any] = None
    action_type: Optional[str] = None
    risk_level: Optional[str] = None
    requires_approval: Optional[bool] = None
    priority: Optional[int] = None
    created_at: Optional[datetime] = None


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: Optional[bool] = None
    requires_approval: Optional[bool] = None


def _get(policy_id: UUID) -> dict:
    res = get_supabase_client().table("policies").select("*").eq("id", str(policy_id)).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Policy not found")
    return res.data[0]


def _loosens(field: str, value: bool, current) -> bool:
    """True if the change lets the agent act more (or with less oversight)."""
    if field == "enabled":
        return value is True and current is not True
    return value is False and current is not False  # requires_approval -> false


def _pending_key(policy_id: str, field: str) -> str:
    return f"{POLICY_PENDING_PREFIX}{policy_id}:{field}"


def _live_pending(db, key: str, value: bool) -> Optional[dict]:
    res = db.table("system_config").select("key, value").eq("key", key).execute()
    if not res.data:
        return None
    try:
        pending = json.loads(res.data[0].get("value") or "")
        requested_at = datetime.fromisoformat(pending["requested_at"])
    except (TypeError, ValueError, KeyError):
        return None
    if pending.get("value") is not value or datetime.now(timezone.utc) - requested_at > PENDING_TTL:
        return None
    return pending


@router.get("/", response_model=List[Policy])
def list_policies():
    return get_supabase_client().table("policies").select("*").order("priority").execute().data


@router.get("/{policy_id}", response_model=Policy)
def get_policy(policy_id: UUID):
    return _get(policy_id)


@router.patch("/{policy_id}", response_model=Policy, dependencies=[rate_limit("policies", 20)])
def update_policy(policy_id: UUID, payload: PolicyUpdate, user: dict = Depends(require_operator)):
    changes = payload.model_dump(exclude_none=True)
    if len(changes) != 1:
        # One field per request keeps each change (and its confirmation) unambiguous.
        raise HTTPException(status_code=400, detail="Set exactly one of: enabled, requires_approval")
    (field, value), = changes.items()
    policy = _get(policy_id)
    pid, actor = str(policy_id), actor_label(user)
    db = get_supabase_client()
    params = {"policy_id": pid, "policy": policy.get("name"), "field": field,
              "from": policy.get(field), "to": value}

    confirmed_from = None
    if settings.REQUIRE_TWO_PERSON_CONFIG and _loosens(field, value, policy.get(field)):
        key = _pending_key(pid, field)
        pending = _live_pending(db, key, value)
        if pending is None:
            now = datetime.now(timezone.utc)
            try:
                AuditLogger().log_action(event_type="policy_change_requested", actor=actor, request_params=params,
                                         message=f"{field}={value} on policy '{policy.get('name')}' "
                                                 "requested; awaiting a second operator", required=True)
            except AuditWriteError:
                raise HTTPException(status_code=503, detail="Audit log unavailable; change not requested")
            db.table("system_config").upsert({
                "key": key,
                "value": json.dumps({"value": value, "requested_by": actor, "requested_at": now.isoformat()}),
                "updated_at": now.isoformat(),
            }, on_conflict="key").execute()
            return JSONResponse(status_code=202, content={
                "status": "pending_confirmation", "policy_id": pid, "field": field, "value": value,
                "expires_at": (now + PENDING_TTL).isoformat()})
        if pending.get("requested_by") == actor:
            raise HTTPException(status_code=409, detail="A different operator must confirm this change")
        confirmed_from = pending.get("requested_by")
        params["requested_by"] = confirmed_from

    try:
        AuditLogger().log_action(event_type="policy_changed", actor=actor, request_params=params,
                                 message=f"Policy '{policy.get('name')}' {field} set to {value}", required=True)
    except AuditWriteError:
        raise HTTPException(status_code=503, detail="Audit log unavailable; change not applied")
    res = db.table("policies").update({field: value}).eq("id", pid).execute()
    if confirmed_from is not None:
        db.table("system_config").delete().eq("key", _pending_key(pid, field)).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Policy not found")
    return res.data[0]
