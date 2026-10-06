"""Read and toggle remediation policies.

Policies were only editable with SQL. Operators can now turn a policy on or
off and choose whether its actions wait for approval. Conditions, action type
and priority stay SQL-only: they change what the agent does, not just whether.

Like the runtime config, a change that lets the agent do more (enabling a
policy, or letting its actions run without approval) waits for a second
operator when REQUIRE_TWO_PERSON_CONFIG is on. Every change is audited first.
"""
from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from backend.app.auth import actor_label, require_operator
from backend.app.config import settings
from backend.app.db.supabase_client import get_supabase_client
from backend.app.rate_limit import rate_limit
from backend.app.services import two_person
from backend.app.services.audit_logger import AuditLogger, AuditWriteError

router = APIRouter()


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


def _audit(event: str, actor: str, params: dict, message: str, detail: str) -> None:
    try:
        AuditLogger().log_action(event_type=event, actor=actor, request_params=params, message=message,
                                 required=True)
    except AuditWriteError:
        raise HTTPException(status_code=503, detail=f"Audit log unavailable; {detail}")


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
    params = {"policy_id": pid, "policy": policy.get("name"), "field": field,
              "from": policy.get(field), "to": value}
    name = policy.get("name")

    pending_key = None
    if settings.REQUIRE_TWO_PERSON_CONFIG and _loosens(field, value, policy.get(field)):
        pending_key = f"POLICY:{pid}:{field}"
        pending = two_person.live_request(pending_key, value)
        if pending is None:
            _audit("policy_change_requested", actor, params,
                   f"{field}={value} on policy '{name}' requested; awaiting a second operator",
                   "change not requested")
            expires_at = two_person.record_request(pending_key, value, actor)
            return JSONResponse(status_code=202, content={
                "status": "pending_confirmation", "policy_id": pid, "field": field, "value": value,
                "expires_at": expires_at})
        if pending.get("requested_by") == actor:
            raise HTTPException(status_code=409, detail="A different operator must confirm this change")
        params["requested_by"] = pending.get("requested_by")

    _audit("policy_changed", actor, params, f"Policy '{name}' {field} set to {value}", "change not applied")
    res = get_supabase_client().table("policies").update({field: value}).eq("id", pid).execute()
    if pending_key:
        two_person.clear(pending_key)
    if not res.data:
        raise HTTPException(status_code=404, detail="Policy not found")
    return res.data[0]
