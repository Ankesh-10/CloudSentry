from fastapi import APIRouter, HTTPException
from typing import List
from backend.app.schemas.models import Resource
from backend.app.db.supabase_client import get_supabase_client
from backend.app.services.discovery import DiscoveryService

router = APIRouter()
db = get_supabase_client()

@router.get("/", response_model=List[Resource])
async def list_resources():
    res = db.table("resources").select("*").execute()
    return res.data

@router.get("/{id}")
async def get_resource(id: str):
    res = db.table("resources").select("*").eq("id", id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Resource not found")
        
    # In a full implementation, we would also fetch metrics_24h, anomalies, and actions here
    return {
        "resource": res.data[0],
        "metrics_24h": [],
        "anomalies": [],
        "actions": []
    }

@router.post("/discover")
async def trigger_discovery():
    try:
        service = DiscoveryService()
        service.run()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
