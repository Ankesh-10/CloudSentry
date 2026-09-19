from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List
from backend.app.schemas.models import Action
from backend.app.db.supabase_client import get_supabase_client
from backend.app.services.executor import ExecutorService
from pydantic import BaseModel
from datetime import datetime, timezone

router = APIRouter()
db = get_supabase_client()
executor = ExecutorService()

class ActionApproval(BaseModel):
    approved: bool
    user_id: str

@router.get("/", response_model=List[Action])
async def list_actions(status: str = None):
    query = db.table("optimization_actions").select("*")
    if status:
        query = query.eq("status", status)
        
    res = query.order("created_at", desc=True).execute()
    return res.data

@router.post("/{id}/approve")
async def approve_action(id: str, payload: ActionApproval, background_tasks: BackgroundTasks):
    """
    Approves or rejects an action. If approved, it is queued for execution.
    """
    res = db.table("optimization_actions").select("*").eq("id", id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Action not found")
        
    action = res.data[0]
    if action["status"] != "pending":
        raise HTTPException(status_code=400, detail="Action is not pending")
        
    now = datetime.now(timezone.utc).isoformat()
    new_status = "approved" if payload.approved else "rejected"
    
    update_res = db.table("optimization_actions").update({
        "status": new_status,
        "approved_by": payload.user_id,
        "approved_at": now
    }).eq("id", id).execute()
    
    if payload.approved:
        # Trigger execution in the background
        background_tasks.add_task(executor.execute_action, id)
        
    return update_res.data[0]
