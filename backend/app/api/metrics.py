from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from backend.app.db.asyncpg_pool import get_pool

router = APIRouter()


def _pool():
    try:
        return get_pool()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Database pool unavailable")


@router.get("/{resource_id}/summary")
async def get_metric_summary(resource_id: UUID):
    rid = resource_id
    pool = _pool()
    query = """
        SELECT DISTINCT ON (metric_name) metric_name, value
        FROM resource_metrics
        WHERE resource_id = $1
        ORDER BY metric_name, time DESC
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(query, rid)
    return {r["metric_name"]: r["value"] for r in records}


@router.get("/{resource_id}")
async def get_metrics(
    resource_id: UUID,
    metric: str | None = Query(default=None, max_length=50),
    days: int = Query(default=1, ge=1, le=90),
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
):
    rid = resource_id
    pool = _pool()
    end = to_ts or datetime.now(timezone.utc)
    start = from_ts or (end - timedelta(days=days))
    if start > end:
        raise HTTPException(status_code=400, detail="'from' must be before 'to'")

    query = """
        SELECT time, metric_name, value, unit
        FROM resource_metrics
        WHERE resource_id = $1 AND time >= $2 AND time <= $3
    """
    args: list = [rid, start, end]
    if metric:
        query += " AND metric_name = $4"
        args.append(metric)
    query += " ORDER BY time DESC LIMIT 5000"

    async with pool.acquire() as conn:
        records = await conn.fetch(query, *args)

    return [{"time": r["time"], "metric_name": r["metric_name"], "value": r["value"], "unit": r["unit"]} for r in records]
