from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict
from backend.app.db.supabase_client import get_supabase_client

router = APIRouter()
db = get_supabase_client()

class SystemConfigUpdate(BaseModel):
    value: str

@router.get("/")
async def get_system_configs():
    res = db.table("system_config").select("*").execute()
    return {item["key"]: item["value"] for item in res.data}

@router.patch("/{key}")
async def update_system_config(key: str, payload: SystemConfigUpdate):
    # Enforce basic validation for known keys
    known_keys = [
        "GLOBAL_AUTOMATION_ENABLED", "DRY_RUN_MODE", 
        "MAX_MONTHLY_BUDGET_USD", "MAX_DAILY_SPEND_USD",
        "MAX_ACTIONS_PER_DAY", "ACTION_COOLDOWN_MINUTES",
        "MAX_CW_API_CALLS_PER_HOUR"
    ]
    if key not in known_keys:
        raise HTTPException(status_code=400, detail="Unknown configuration key")
        
    res = db.table("system_config").update({"value": payload.value}).eq("key", key).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Configuration not found")
        
    # Note: In a robust setup, updating a config here would also update the in-memory `settings` 
    # if it relies on DB values instead of env vars, or trigger a reload.
    
    return {"status": "success", "key": key, "value": payload.value}
