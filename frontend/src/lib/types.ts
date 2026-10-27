// Mirrors backend/app/schemas/models.py and the dict responses in backend/app/api.
// Datetimes are ISO-8601 strings.

export const RESOURCE_TYPES = ['ec2', 'lambda', 's3', 'rds', 'ebs'] as const

export interface Resource {
  id: string
  provider_id: string
  resource_type: string
  name: string | null
  region: string | null
  state: string | null
  tags: Record<string, unknown> | null
  protected: boolean | null
  metadata: Record<string, unknown> | null
  account_id: string | null
  first_seen: string | null
  last_seen: string | null
}

export type AnomalyStatus = 'active' | 'resolved' | 'false_positive'
export type Severity = 'LOW' | 'MEDIUM' | 'HIGH'

export interface Anomaly {
  id: string
  resource_id: string | null
  anomaly_type: string | null
  severity: Severity | null
  anomaly_score: number | null
  confidence: number | null
  reason: string | null
  features_snapshot: Record<string, unknown> | null
  model_version: string | null
  status: AnomalyStatus | null
  detected_at: string | null
  resolved_at: string | null
}

export const ACTION_STATUSES = [
  'pending',
  'pending_approval',
  'approved',
  'rejected',
  'executing',
  'completed',
  'failed',
  'rolled_back',
] as const
export type ActionStatus = (typeof ACTION_STATUSES)[number]

// backend/app/services/action_runner.py REVERSIBLE
export const REVERSIBLE_ACTIONS = new Set(['stop_ec2', 'limit_lambda', 'apply_tags'])

export interface Action {
  id: string
  anomaly_id: string | null
  resource_id: string | null
  action_type: string | null
  risk_level: string | null
  status: ActionStatus | null
  dry_run: boolean | null
  requires_approval: boolean | null
  pre_state: Record<string, unknown> | null
  post_state: Record<string, unknown> | null
  estimated_savings_usd: number | null
  approved_by: string | null
  approved_at: string | null
  created_at: string | null
  claimed_at: string | null
  executed_at: string | null
  verified_at: string | null
  rollback_action_id: string | null
}

export interface AuditLog {
  id: string
  event_type: string | null
  actor: string | null
  resource_id: string | null
  action_id: string | null
  aws_api_call: string | null
  request_params: Record<string, unknown> | null
  response_status: string | null
  message: string | null
  request_id: string | null
  client_ip: string | null
  created_at: string
}

export interface MetricPoint {
  time: string
  metric_name: string
  value: number
  unit: string | null
}

export interface ResourceBundle {
  resource: Resource
  anomalies: Anomaly[]
  actions: Action[]
  metrics_24h: MetricPoint[] | null
}

export interface DashboardSummary {
  active_resources: number
  active_anomalies: number
  potential_savings_usd: number
  realized_savings_usd: number
  month_to_date_cost_estimated_usd: number
  automation_enabled: boolean
  dry_run: boolean
}

export interface CostPoint {
  date: string
  cost_usd: number
}

export interface AnomalySummary {
  HIGH: number
  MEDIUM: number
  LOW: number
  total: number
}

export interface JobStatus {
  last_success?: string | null
  last_failure?: string | null
  last_error?: string | null
  consecutive_failures?: number
}

export interface SystemHealth {
  status: 'ok' | 'degraded' | 'unhealthy'
  db: { status: string; latency_ms?: number }
  ml_model: {
    status?: string
    models?: string[]
    mode?: string
    details?: Record<string, Record<string, unknown>>
  }
  telemetry: { last_collection: string | null; staleness_minutes: number | null }
  automation: { enabled: boolean; dry_run: boolean; two_person?: TwoPersonMode }
  scheduler: { running: boolean; leader: boolean }
  jobs: Record<string, JobStatus>
  budget?: { over_budget: boolean; reason: string | null }
  issues: string[]
}

// backend/app/services/runtime_config.py KNOWN_KEYS
export type RuntimeConfig = Record<string, boolean | number>

export type ConfigChangeResult =
  | { status: 'success'; key: string; value: boolean | number }
  | { status: 'pending_confirmation'; key: string; value: boolean | number; expires_at: string; confirmable_at?: string | null }

// Who confirms a change that loosens a limit (backend TWO_PERSON_MODE).
export type TwoPersonMode = 'off' | 'two_person' | 'solo_delay'

// GET /system/config/pending. `key` is a config key, POLICY:<id>:<field> or
// RESOURCE:<id>:protected; confirming means repeating the same change.
export interface PendingChange {
  key: string
  value: string | boolean | number | null
  requested_by: string | null
  requested_at: string
  expires_at: string
  expired: boolean
  // When the requester may confirm it themselves; null: only another operator.
  confirmable_at: string | null
}

export interface EmergencyStopResult {
  automation_enabled: false
  persisted: boolean
  audit_logged: boolean
  config: RuntimeConfig
}
