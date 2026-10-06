from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from backend.app.db.pagination import set_total
from backend.app.db.supabase_client import get_supabase_client

router = APIRouter()


class AuditLog(BaseModel):
    id: str
    event_type: Optional[str] = None
    actor: Optional[str] = None
    resource_id: Optional[str] = None
    action_id: Optional[str] = None
    aws_api_call: Optional[str] = None
    request_params: Optional[dict[str, Any]] = None
    response_status: Optional[str] = None
    message: Optional[str] = None
    request_id: Optional[str] = None
    client_ip: Optional[str] = None
    created_at: datetime


@router.get("/", response_model=List[AuditLog])
def list_audit_logs(
    response: Response,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=100_000),
    resource_id: Optional[UUID] = None,
    action_id: Optional[UUID] = None,
    event_type: Optional[str] = Query(default=None, max_length=50, pattern=r"^[a-z_]+$"),
    actor: Optional[str] = Query(default=None, max_length=50),
    request_id: Optional[str] = Query(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
):
    if since and until and since > until:
        raise HTTPException(status_code=400, detail="'since' must be before 'until'")
    query = get_supabase_client().table("audit_logs").select("*", count="exact")
    if resource_id:
        query = query.eq("resource_id", str(resource_id))
    if action_id:
        query = query.eq("action_id", str(action_id))
    if event_type:
        query = query.eq("event_type", event_type)
    if actor:
        query = query.eq("actor", actor)
    if request_id:
        query = query.eq("request_id", request_id)
    if since:
        query = query.gte("created_at", since.isoformat())
    if until:
        query = query.lt("created_at", until.isoformat())
    res = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    set_total(response, res)
    return res.data
