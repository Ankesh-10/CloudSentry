from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Response models mirror db/migrations/001_initial_schema.sql. Every column the
# schema allows to be NULL is Optional here; otherwise a valid row (e.g. a
# rollback action with no anomaly) turns into a 500 at response validation.


class ResourceBase(BaseModel):
    provider_id: str
    resource_type: str
    name: Optional[str] = None
    region: Optional[str] = None
    state: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    protected: Optional[bool] = False
    metadata: Optional[Dict[str, Any]] = None


class Resource(ResourceBase):
    id: UUID
    account_id: Optional[UUID] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class MetricBase(BaseModel):
    metric_name: str
    value: float
    unit: Optional[str] = None


class Metric(MetricBase):
    time: datetime
    resource_id: UUID
    model_config = ConfigDict(from_attributes=True)


class AnomalyBase(BaseModel):
    anomaly_type: Optional[str] = None
    severity: Optional[str] = None
    anomaly_score: Optional[float] = None
    confidence: Optional[float] = None
    reason: Optional[str] = None
    features_snapshot: Optional[Dict[str, Any]] = None
    model_version: Optional[str] = None
    status: Optional[str] = "active"


class Anomaly(AnomalyBase):
    id: UUID
    resource_id: Optional[UUID] = None
    detected_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class ActionBase(BaseModel):
    action_type: Optional[str] = None
    risk_level: Optional[str] = None
    status: Optional[str] = "pending"
    dry_run: Optional[bool] = True
    requires_approval: Optional[bool] = False
    pre_state: Optional[Dict[str, Any]] = None
    post_state: Optional[Dict[str, Any]] = None
    estimated_savings_usd: Optional[float] = None


class Action(ActionBase):
    id: UUID
    anomaly_id: Optional[UUID] = None
    resource_id: Optional[UUID] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    rollback_action_id: Optional[UUID] = None
    model_config = ConfigDict(from_attributes=True)
