from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.app.auth import actor_label, require_operator
from backend.app.db.supabase_client import get_supabase_client
from backend.app.schemas.models import Anomaly
from backend.app.services.audit_logger import AuditLogger

router = APIRouter()

AnomalyStatus = Literal["active", "resolved", "false_positive"]


class AnomalyUpdate(BaseModel):
    status: AnomalyStatus


@router.get("/", response_model=List[Anomaly])
def list_anomalies(
    status: Optional[AnomalyStatus] = None,
    resource_id: Optional[UUID] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    query = get_supabase_client().table("anomalies").select("*")
    if status:
        query = query.eq("status", status)
    if resource_id:
        query = query.eq("resource_id", str(resource_id))
    return query.order("detected_at", desc=True).range(offset, offset + limit - 1).execute().data


@router.get("/{anomaly_id}", response_model=Anomaly)
def get_anomaly(anomaly_id: UUID):
    res = get_supabase_client().table("anomalies").select("*").eq("id", str(anomaly_id)).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    return res.data[0]


def _update_status(anomaly_id: UUID, payload: AnomalyUpdate, user: dict):
    update = {"status": payload.status}
    update["resolved_at"] = None if payload.status == "active" else datetime.now(timezone.utc).isoformat()
    try:
        res = get_supabase_client().table("anomalies").update(update).eq("id", str(anomaly_id)).execute()
    except Exception:
        # Re-activating can collide with uq_anomalies_one_active.
        raise HTTPException(status_code=409, detail="Another active anomaly of this type exists for the resource")
    if not res.data:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    row = res.data[0]
    AuditLogger().log_action(
        event_type="anomaly_status_changed",
        actor=actor_label(user),
        resource_id=row.get("resource_id"),
        message=f"Anomaly {anomaly_id} set to {payload.status}",
    )
    return row


@router.patch("/{anomaly_id}/status", response_model=Anomaly)
def update_anomaly_status_path(anomaly_id: UUID, payload: AnomalyUpdate, user: dict = Depends(require_operator)):
    return _update_status(anomaly_id, payload, user)


@router.patch("/{anomaly_id}", response_model=Anomaly)
def update_anomaly_status(anomaly_id: UUID, payload: AnomalyUpdate, user: dict = Depends(require_operator)):
    return _update_status(anomaly_id, payload, user)
