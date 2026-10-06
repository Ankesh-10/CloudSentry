import logging
from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from backend.app.auth import actor_label, require_operator
from backend.app.db.pagination import set_total
from backend.app.db.supabase_client import get_supabase_client
from backend.app.schemas.models import Anomaly
from backend.app.services.audit_logger import AuditLogger, AuditWriteError

logger = logging.getLogger(__name__)

router = APIRouter()

AnomalyStatus = Literal["active", "resolved", "false_positive"]


class AnomalyUpdate(BaseModel):
    status: AnomalyStatus


@router.get("/", response_model=List[Anomaly])
def list_anomalies(
    response: Response,
    status: Optional[AnomalyStatus] = None,
    resource_id: Optional[UUID] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    query = get_supabase_client().table("anomalies").select("*", count="exact")
    if status:
        query = query.eq("status", status)
    if resource_id:
        query = query.eq("resource_id", str(resource_id))
    res = query.order("detected_at", desc=True).range(offset, offset + limit - 1).execute()
    set_total(response, res)
    return res.data


@router.get("/{anomaly_id}", response_model=Anomaly)
def get_anomaly(anomaly_id: UUID):
    res = get_supabase_client().table("anomalies").select("*").eq("id", str(anomaly_id)).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    return res.data[0]


def _update_status(anomaly_id: UUID, payload: AnomalyUpdate, user: dict):
    db = get_supabase_client()
    existing = db.table("anomalies").select("id, resource_id, status").eq("id", str(anomaly_id)).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    current = existing.data[0]
    # Audit first: no status change without a record of who made it.
    try:
        AuditLogger().log_action(
            event_type="anomaly_status_changed",
            actor=actor_label(user),
            resource_id=current.get("resource_id"),
            request_params={"anomaly_id": str(anomaly_id), "from": current.get("status"), "to": payload.status},
            message=f"Anomaly {anomaly_id} set to {payload.status}",
            required=True,
        )
    except AuditWriteError:
        raise HTTPException(status_code=503, detail="Audit log unavailable; change not applied")

    update = {"status": payload.status}
    update["resolved_at"] = None if payload.status == "active" else datetime.now(timezone.utc).isoformat()
    try:
        res = db.table("anomalies").update(update).eq("id", str(anomaly_id)).execute()
    except Exception as e:
        # Re-activating can collide with uq_anomalies_one_active; anything else
        # is a real failure and must not be reported as a conflict.
        if payload.status == "active" and "duplicate" in str(e).lower():
            raise HTTPException(status_code=409, detail="Another active anomaly of this type exists for the resource")
        logger.exception("Anomaly status update failed for %s", anomaly_id)
        raise HTTPException(status_code=500, detail="Failed to update anomaly")
    if not res.data:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    return res.data[0]


@router.patch("/{anomaly_id}/status", response_model=Anomaly)
def update_anomaly_status_path(anomaly_id: UUID, payload: AnomalyUpdate, user: dict = Depends(require_operator)):
    return _update_status(anomaly_id, payload, user)


@router.patch("/{anomaly_id}", response_model=Anomaly)
def update_anomaly_status(anomaly_id: UUID, payload: AnomalyUpdate, user: dict = Depends(require_operator)):
    return _update_status(anomaly_id, payload, user)
