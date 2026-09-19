from fastapi import APIRouter
from backend.app.db.supabase_client import get_supabase_client

router = APIRouter()
db = get_supabase_client()

@router.get("/summary")
async def get_dashboard_summary():
    """
    Returns high-level aggregate metrics for the dashboard.
    """
    # 1. Count active resources
    res = db.table("resources").select("id", count="exact").neq("state", "terminated").execute()
    active_resources_count = res.count
    
    # 2. Count active anomalies
    res = db.table("anomalies").select("id", count="exact").eq("status", "active").execute()
    active_anomalies_count = res.count
    
    # 3. Sum estimated savings from pending/approved actions
    res = db.table("optimization_actions").select("estimated_savings_usd").in_("status", ["pending", "approved"]).execute()
    potential_savings = sum(float(a["estimated_savings_usd"] or 0) for a in res.data)
    
    # 4. Sum realized savings from completed actions
    res = db.table("optimization_actions").select("estimated_savings_usd").eq("status", "completed").execute()
    realized_savings = sum(float(a["estimated_savings_usd"] or 0) for a in res.data)
    
    return {
        "active_resources": active_resources_count,
        "active_anomalies": active_anomalies_count,
        "potential_savings_usd": potential_savings,
        "realized_savings_usd": realized_savings
    }
