from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.app.db.supabase_client import get_supabase_client

router = APIRouter()


class AuditLog(BaseModel):
    id: str
    event_type: Optional[str] = None
    actor: Optional[str] = None
    resource_id: Optional[str] = None
    action_id: Optional[str] = None
    aws_api_call: Optional[str] = None
    response_status: Optional[str] = None
    message: Optional[str] = None
    created_at: datetime


@router.get("/", response_model=List[AuditLog])
def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    resource_id: Optional[UUID] = None,
    action_id: Optional[UUID] = None,
):
    query = get_supabase_client().table("audit_logs").select("*")
    if resource_id:
        query = query.eq("resource_id", str(resource_id))
    if action_id:
        query = query.eq("action_id", str(action_id))
    return query.order("created_at", desc=True).range(offset, offset + limit - 1).execute().data
