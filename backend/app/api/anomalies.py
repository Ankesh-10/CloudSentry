from fastapi import APIRouter, HTTPException
from typing import List
from backend.app.schemas.models import Anomaly
from backend.app.db.supabase_client import get_supabase_client
from pydantic import BaseModel

router = APIRouter()
db = get_supabase_client()

class AnomalyUpdate(BaseModel):
    status: str

@router.get("/", response_model=List[Anomaly])
async def list_anomalies(status: str = None, resource_id: str = None):
    query = db.table("anomalies").select("*")
    if status:
        query = query.eq("status", status)
    if resource_id:
        query = query.eq("resource_id", resource_id)
        
    res = query.order("detected_at", desc=True).execute()
    return res.data

@router.get("/{id}", response_model=Anomaly)
async def get_anomaly(id: str):
    res = db.table("anomalies").select("*").eq("id", id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    return res.data[0]

@router.patch("/{id}")
async def update_anomaly_status(id: str, payload: AnomalyUpdate):
    if payload.status not in ["active", "resolved", "false_positive"]:
        raise HTTPException(status_code=400, detail="Invalid status")
        
    res = db.table("anomalies").update({"status": payload.status}).eq("id", id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    return res.data[0]
