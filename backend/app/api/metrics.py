from fastapi import APIRouter, HTTPException
from typing import List
from backend.app.db.supabase_client import get_supabase_client
from backend.app.db.asyncpg_pool import get_pool
from datetime import datetime, timedelta

router = APIRouter()
db = get_supabase_client()

@router.get("/{resource_id}")
async def get_metrics(resource_id: str, metric: str = None, days: int = 1):
    pool = get_pool()
    query = """
        SELECT time, value, unit 
        FROM resource_metrics 
        WHERE resource_id = $1
    """
    args = [resource_id]
    
    if metric:
        query += " AND metric_name = $2"
        args.append(metric)
        
    query += " ORDER BY time DESC LIMIT 1000"
    
    async with pool.acquire() as conn:
        records = await conn.fetch(query, *args)
        
    return [{"time": r["time"], "value": r["value"], "unit": r["unit"]} for r in records]

@router.get("/{resource_id}/summary")
async def get_metric_summary(resource_id: str):
    pool = get_pool()
    query = """
        SELECT metric_name, value 
        FROM resource_metrics 
        WHERE resource_id = $1 
        ORDER BY time DESC LIMIT 10
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(query, resource_id)
        
    summary = {}
    for r in records:
        if r["metric_name"] not in summary:
            summary[r["metric_name"]] = r["value"]
            
    return summary
