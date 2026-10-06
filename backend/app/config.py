from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""
    SUPABASE_JWKS_URL: str = ""  # default: <SUPABASE_URL>/auth/v1/.well-known/jwks.json
    JWT_AUDIENCE: str = "authenticated"
    JWT_ISSUER: str = ""  # default: <SUPABASE_URL>/auth/v1
    # Comma-separated Supabase user ids allowed to mutate cloud state / safety config.
    # Users can also be granted app_metadata.cloudsentry_role = "operator".
    OPERATOR_USER_IDS: str = ""
    # Comma-separated user ids allowed to read (or app_metadata.cloudsentry_role
    # = "viewer"). Operators are always viewers.
    VIEWER_USER_IDS: str = ""
    # false: any verified, non-anonymous user may read (only safe when Supabase
    # sign-up is closed to your own team).
    REQUIRE_VIEWER_ROLE: bool = True
    DATABASE_URL: str = ""

    # AWS
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_DEFAULT_REGION: str = "us-east-1"
    # Comma-separated regions to discover/monitor (default: AWS_DEFAULT_REGION
    # only). The IAM policy must allow each (see cloud_permissions/README.md).
    AWS_REGIONS: str = ""
    CLOUD_ACCOUNT_ID: str = ""  # recorded on cloud_accounts; informational

    # Multi-Cloud
    CLOUD_PROVIDER: str = "aws"  # 'aws' or 'gcp'

    # GCP
    GCP_PROJECT_ID: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    # Backend
    BACKEND_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"
    ENVIRONMENT: str = "production"  # shown in alerts
    # Observability. Alerts go to a Slack-compatible incoming webhook (https).
    ALERT_WEBHOOK_URL: str = ""
    ALERT_MIN_INTERVAL_SECONDS: int = 900  # per alert key
    # /metrics (Prometheus text) is served only when set, behind this bearer token.
    METRICS_TOKEN: str = ""
    # /docs, /redoc and /openapi.json map every endpoint; keep them off in prod.
    EXPOSE_API_DOCS: bool = False
    # Per-client-IP ceiling across all endpoints (per process). Sensitive
    # endpoints have tighter per-user limits in code.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_PER_MINUTE: int = 300
    # Run background jobs in this process. Leader election still guarantees a
    # single active scheduler across replicas; set false for API-only replicas.
    SCHEDULER_ENABLED: bool = True
    # Job cadence (leader only).
    JOB_DISCOVERY_MINUTES: int = 15
    JOB_PIPELINE_MINUTES: int = 5      # telemetry -> detection -> policy
    JOB_EXECUTE_MINUTES: int = 2
    JOB_VERIFY_MINUTES: int = 2
    JOB_RETRAIN_HOUR_UTC: int = 2
    JOB_RETENTION_HOUR_UTC: int = 3

    # ML
    ML_MODEL_PATH: str = ""  # default: <repo>/models
    # Applied to IsolationForest.decision_function (0 = the boundary learned from
    # ML_CONTAMINATION; negative = stricter). Renamed from ML_ANOMALY_THRESHOLD,
    # which was compared against score_samples (-0.3 there flagged ~every point):
    # a new name so an old value is never silently reinterpreted on a new scale.
    ML_IF_DECISION_THRESHOLD: float = 0.0
    ML_ANOMALY_THRESHOLD: Optional[float] = None  # deprecated, ignored (warned at startup)
    ML_CONTAMINATION: float = 0.05
    # Training: threads (small instances: keep 1), sample cap per resource type,
    # promotion gate (max share of the recent holdout a new model may flag), and
    # the drift level that is logged as a warning.
    ML_N_JOBS: int = 1
    ML_MAX_TRAINING_ROWS: int = 50_000
    ML_MAX_HOLDOUT_FLAG_RATE: float = 0.25
    ML_DRIFT_WARN: float = 1.0
    ML_MODEL_STALE_HOURS: int = 72
    ML_ZSCORE_THRESHOLD: float = 2.5
    ML_MIN_POINTS_ZSCORE: int = 144
    ML_MIN_POINTS_IF: int = 2016
    ML_IDLE_CPU_THRESHOLD_PCT: float = 5.0
    ML_IDLE_WINDOW_HOURS: float = 2.0
    LAMBDA_CONCURRENCY_LIMIT: int = 10
    REQUIRED_TAGS: str = "Project,Owner"

    # Safety
    GLOBAL_AUTOMATION_ENABLED: bool = False
    DRY_RUN_MODE: bool = True
    MAX_MONTHLY_BUDGET_USD: float = 10.00
    MAX_DAILY_SPEND_USD: float = 2.00
    MAX_ACTIONS_PER_DAY: int = 5
    ACTION_COOLDOWN_MINUTES: int = 30
    MAX_CW_API_CALLS_PER_HOUR: int = 200
    # GetMetricData is billed per metric requested ($0.01 per 1,000): this is the
    # cost bound. 2000/hour caps CloudWatch at about $15/month.
    MAX_CW_METRICS_PER_HOUR: int = 2000
    # Tag writes (apply/remove) are exempt from MAX_ACTIONS_PER_DAY; this caps them.
    MAX_TAG_ACTIONS_PER_DAY: int = 50
    # Proposals/approvals older than this are expired instead of executed.
    APPROVAL_TTL_HOURS: int = 72
    # Retention (days, minimum 90 — the audit_logs trigger refuses younger deletes).
    AUDIT_RETENTION_DAYS: int = 365
    HISTORY_RETENTION_DAYS: int = 365  # finished actions, closed anomalies
    # Enabling automation or disabling dry-run needs a second operator to confirm.
    REQUIRE_TWO_PERSON_CONFIG: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def cors_origin_list(self) -> list[str]:
        return [o for o in _split(self.CORS_ORIGINS) if o != "*"]

    def operator_user_id_set(self) -> set[str]:
        return set(_split(self.OPERATOR_USER_IDS))

    def viewer_user_id_set(self) -> set[str]:
        return set(_split(self.VIEWER_USER_IDS))

    def aws_region_list(self) -> list[str]:
        """Configured regions, default region first, de-duplicated."""
        regions = [self.AWS_DEFAULT_REGION] + _split(self.AWS_REGIONS)
        return list(dict.fromkeys(r for r in regions if r))

    def required_tag_list(self) -> list[str]:
        return _split(self.REQUIRED_TAGS)


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


settings = Settings()
