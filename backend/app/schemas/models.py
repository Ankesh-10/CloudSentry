from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID

class ResourceBase(BaseModel):
    provider_id: str
    resource_type: str
    name: Optional[str] = None
    region: Optional[str] = None
    state: Optional[str] = None
    tags: Optional[Dict[str, Any]] = None
    protected: bool = False
    metadata: Optional[Dict[str, Any]] = None

class Resource(ResourceBase):
    id: UUID
    account_id: UUID
    first_seen: datetime
    last_seen: datetime
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
    anomaly_type: str
    severity: str
    anomaly_score: float
    confidence: float
    reason: str
    features_snapshot: Optional[Dict[str, Any]] = None
    model_version: str
    status: str = "active"

class Anomaly(AnomalyBase):
    id: UUID
    resource_id: UUID
    detected_at: datetime
    resolved_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class ActionBase(BaseModel):
    action_type: str
    risk_level: str
    status: str = "pending"
    dry_run: bool = True
    requires_approval: bool = False
    pre_state: Optional[Dict[str, Any]] = None
    post_state: Optional[Dict[str, Any]] = None
    estimated_savings_usd: Optional[float] = None

class Action(ActionBase):
    id: UUID
    anomaly_id: UUID
    resource_id: UUID
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    executed_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    rollback_action_id: Optional[UUID] = None
    model_config = ConfigDict(from_attributes=True)
