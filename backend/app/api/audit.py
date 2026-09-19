from fastapi import APIRouter
from typing import List, Optional
from backend.app.db.supabase_client import get_supabase_client
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()
db = get_supabase_client()

class AuditLog(BaseModel):
    id: str
    event_type: str
    actor: str
    resource_id: Optional[str]
    action_id: Optional[str]
    aws_api_call: Optional[str]
    response_status: Optional[str]
    message: Optional[str]
    created_at: datetime

@router.get("/", response_model=List[AuditLog])
async def list_audit_logs(limit: int = 50, offset: int = 0):
    res = db.table("audit_logs").select("*").order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return res.data
