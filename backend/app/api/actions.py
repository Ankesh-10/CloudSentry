from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import List
from backend.app.schemas.models import Action
from backend.app.db.supabase_client import get_supabase_client
from backend.app.services.action_runner import ActionRunner
from pydantic import BaseModel
from datetime import datetime, timezone

router = APIRouter()
db = get_supabase_client()
runner = ActionRunner()

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
    if action["status"] not in ["pending", "pending_approval"]:
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
        background_tasks.add_task(runner.execute_action, id)
        
    return update_res.data[0]

@router.post("/{id}/rollback")
async def rollback_action(id: str, background_tasks: BackgroundTasks, user_id: str = "API_USER"):
    """
    Triggers a rollback for a completed reversible action.
    """
    res = db.table("optimization_actions").select("*").eq("id", id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Action not found")
        
    action = res.data[0]
    if action["status"] != "completed":
        raise HTTPException(status_code=400, detail="Can only rollback completed actions")
        
    if action.get("rollback_action_id"):
        raise HTTPException(status_code=400, detail="Action has already been rolled back")
        
    background_tasks.add_task(runner.rollback_action, id, user_id)
    return {"message": "Rollback initiated"}
