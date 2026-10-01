from fastapi import APIRouter, HTTPException, Query

from backend.app.db.asyncpg_pool import get_pool
from backend.app.services import runtime_config

router = APIRouter()

# Aggregates run in SQL: summing rows fetched through PostgREST silently
# truncates at its 1000-row cap and blocks the event loop.


def _pool():
    try:
        return get_pool()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Database pool unavailable")


@router.get("/overview")
@router.get("/summary")
async def get_dashboard_summary():
    async with _pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM resources WHERE state IS DISTINCT FROM 'terminated'
                                             AND state IS DISTINCT FROM 'deleted')            AS active_resources,
              (SELECT COUNT(*) FROM anomalies WHERE status = 'active')                         AS active_anomalies,
              (SELECT COALESCE(SUM(estimated_savings_usd), 0) FROM optimization_actions
                 WHERE status IN ('pending', 'pending_approval', 'approved'))                  AS potential_savings,
              (SELECT COALESCE(SUM(estimated_savings_usd), 0) FROM optimization_actions
                 WHERE status = 'completed' AND dry_run = FALSE)                               AS realized_savings,
              (SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM cost_records
                 WHERE recorded_at >= date_trunc('month', NOW()))                              AS month_to_date_cost
            """
        )
    flags = runtime_config.snapshot()
    return {
        "active_resources": row["active_resources"],
        "resource_count": row["active_resources"],
        "active_anomalies": row["active_anomalies"],
        "anomaly_count": row["active_anomalies"],
        "potential_savings_usd": float(row["potential_savings"]),
        "realized_savings_usd": float(row["realized_savings"]),
        "savings_achieved": float(row["realized_savings"]),
        "total_cost_est": float(row["month_to_date_cost"]),
        "month_to_date_cost_estimated_usd": float(row["month_to_date_cost"]),
        "automation_status": flags.get("GLOBAL_AUTOMATION_ENABLED"),
        "automation_enabled": flags.get("GLOBAL_AUTOMATION_ENABLED"),
        "dry_run": flags.get("DRY_RUN_MODE"),
    }


@router.get("/cost-trend")
async def get_cost_trend(days: int = Query(default=7, ge=1, le=90)):
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT (recorded_at AT TIME ZONE 'UTC')::date AS day, SUM(estimated_cost_usd) AS cost
            FROM cost_records
            WHERE recorded_at >= (NOW() AT TIME ZONE 'UTC')::date - ($1::int - 1)
            GROUP BY day
            ORDER BY day
            """,
            days,
        )
    return [{"date": r["day"].isoformat(), "cost_usd": float(r["cost"] or 0)} for r in rows]


@router.get("/anomaly-summary")
async def get_anomaly_summary():
    async with _pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT UPPER(COALESCE(severity, 'LOW')) AS sev, COUNT(*) AS n FROM anomalies WHERE status = 'active' GROUP BY 1"
        )
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for r in rows:
        if r["sev"] in counts:
            counts[r["sev"]] = r["n"]
    counts["total"] = sum(counts.values())
    return counts
