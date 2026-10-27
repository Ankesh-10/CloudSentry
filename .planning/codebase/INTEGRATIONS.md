---
last_mapped_commit: 7c29211f4ff4cdfd1dad8c9001b1fadd2b22c34f
last_mapped_at: 2026-10-23
---
# External Integrations

**Analysis Date:** 2026-10-23

## APIs & External Services

**Cloud Provider - AWS (primary):**
- Services used via boto3: EC2 (instances, EBS volumes), Lambda, S3, RDS, CloudWatch (metrics for anomaly detection and telemetry)
  - Client construction: `boto3.client(...)` in `backend/app/adapters/aws.py` (lines ~29-33), shared `boto_config` retry/timeout config
  - Adapter selection: `backend/app/adapters/factory.py` (`CLOUD_PROVIDER == "aws"`)
  - Auth: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` (or the default boto3 credential chain)
  - Multi-region scope: `AWS_REGIONS` setting in `backend/app/config.py`
  - Scoped by a custom IAM policy: `terraform/iam.tf`, reviewed by `tests/test_iam_policy.py`
- Demo fixtures provisioned by Terraform: `terraform/main.tf` (EC2 idle/active instances, orphaned EBS volume, S3 bucket with public access block, Lambda function and IAM role)

**Cloud Provider - Google Cloud (secondary, selectable):**
- Services: Compute Engine (`google-cloud-compute`, instance list/stop/start via `compute_client`), Cloud Monitoring (`google-cloud-monitoring`, `monitoring_v3`), Resource Manager (`google-cloud-resource-manager`)
  - Implementation: `backend/app/adapters/gcp.py`; clients may be injected for tests
  - Auth: `GOOGLE_APPLICATION_CREDENTIALS` (service account JSON path) and `GCP_PROJECT_ID`
  - Permissions defined in `cloud_permissions/gcp_custom_role.yaml`
  - Selected by `CLOUD_PROVIDER=gcp`

**Supabase (platform backend):**
- Purpose: authentication, PostgREST data access, and the hosted Postgres database
- Backend client: `supabase` Python SDK, created in `backend/app/db/supabase_client.py` with the service role key (`auto_refresh_token=False`, `persist_session=False`, timeout `POSTGREST_TIMEOUT_SECONDS`); used across `backend/app/api/*.py` and `backend/app/services/*.py`
- Frontend client: `@supabase/supabase-js` in `frontend/src/lib/supabase.ts`; sign-in with password (`signInWithPassword`), password reset (`resetPasswordForEmail`, `updateUser`), session listener (`onAuthStateChange`) in `frontend/src/auth/AuthProvider.tsx`
- Auth: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (backend, secret), `SUPABASE_JWT_SECRET` (HS256 fallback), `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` / `VITE_SUPABASE_ANON_KEY` (frontend, public)
- Supabase tooling reference in `scripts/test_supabase.py` and `scripts/inject_anomaly.py`, `scripts/telemetry_demo.py` (also create clients)

**Alerting webhook (optional):**
- Slack-compatible incoming webhook (`{"text": ...}` payload)
  - Implementation: `backend/app/services/alerts.py` using `httpx.post` with a 5 s timeout; rate-limited by `ALERT_MIN_INTERVAL_SECONDS`
  - Config: `ALERT_WEBHOOK_URL` (`backend/app/config.py`, set in Render dashboard)

**Caller-side ML models:**
- No external ML API. Models are local scikit-learn artifacts saved to `ML_MODEL_PATH` (`/app/models` in production) and loaded with `joblib` in `ml/inference.py`

## Data Storage

**Primary database:**
- PostgreSQL hosted on Supabase
  - Access path 1: Supabase PostgREST via `supabase` Python client (`backend/app/db/supabase_client.py`) and `@supabase/supabase-js` in the browser
  - Access path 2: direct asyncpg pool for time-series queries (`backend/app/db/asyncpg_pool.py`); DSN in `DATABASE_URL` (session pooler on port 5432 or direct; port 6543 is explicitly avoided per `render.yaml`)
  - Schema migrations: `db/migrations/*.sql`, applied by `scripts/apply_migrations.py` (Render `preDeployCommand`); version tracked in `schema_migrations` table
  - Row-level security: `db/migrations/003_rls_policies.sql`, `007_integrity_and_rls.sql`, `009_grants_and_constraints.sql`
- Leader election for the scheduler uses a database lock: `backend/app/db/leader.py`

**File Storage:**
- Local filesystem only for trained ML model artifacts (`/app/models`, Render persistent disk `cloudsentry-models`, 1 GB)
- Local fixtures under `data/` and `ml/synthetic/scenarios/`

**Caching:**
- None external. In-process caches only: JWKS cache in `backend/app/auth.py` (TTL plus minimum refresh interval), Supabase client singleton in `backend/app/db/supabase_client.py`, in-process metrics in `backend/app/metrics.py`

## Authentication & Identity

**Auth Provider:**
- Supabase Auth (email and password)
  - Browser: `frontend/src/auth/AuthProvider.tsx`, `frontend/src/pages/Login.tsx`, `frontend/src/pages/ResetPassword.tsx`
  - Backend verification: `backend/app/auth.py`
    - Preferred: asymmetric tokens verified against JWKS fetched from `SUPABASE_JWKS_URL` (default `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`), cached with TTL
    - Fallback: HS256 verification with `SUPABASE_JWT_SECRET`
    - Checks: audience `JWT_AUDIENCE` (default `authenticated`), issuer `JWT_ISSUER` (default `<SUPABASE_URL>/auth/v1`)
  - Authorization: role lists from env vars `OPERATOR_USER_IDS` and `VIEWER_USER_IDS`; `REQUIRE_VIEWER_ROLE=true` by default
  - Two-person approval for configuration changes: `backend/app/services/two_person.py` (`REQUIRE_TWO_PERSON_CONFIG`)
  - Frontend role helper: `frontend/src/lib/role.ts`

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry or similar SDK detected)

**Metrics:**
- Built-in `/metrics` endpoint in `backend/app/metrics.py` (also mounted at `/api/v1/metrics` in `backend/app/main.py`), disabled unless `METRICS_TOKEN` is set; then requires `Authorization: Bearer <METRICS_TOKEN>`

**Logs:**
- Python `logging` with JSON or text formatter (`LOG_FORMAT`, `LOG_LEVEL`) configured in `backend/app/logging_config.py`
- Audit trail persisted to Supabase tables via `backend/app/services/audit_logger.py`
- Health endpoints: `/api/v1/health/live` (liveness, no DB), `/api/v1/health` (readiness, 503 when DB unreachable), `/api/v1/system/health` (ML model state, `ml_model.writable`)

**Job Status:**
- Persisted job runs (`db/migrations/011_schema_version_and_job_runs.sql`), read by `backend/app/services/job_status.py`

## CI/CD & Deployment

**Hosting:**
- Render.com, defined in `render.yaml`
  - `cloudsentry-backend`: Docker web service from `backend/Dockerfile`
  - `cloudsentry-frontend`: static site from `frontend/`

**CI Pipeline:**
- GitHub Actions, `.github/workflows/ci.yml` (triggers: push to `main`, pull requests)
  - `lint`: `ruff check .`
  - `security`: gitleaks secret scan; `pip-audit -r backend/requirements.txt --strict`
  - `test`: Python 3.11 and 3.12 matrix; `pytest` with coverage (`--cov-fail-under=70`); `python -m ml.evaluation --strict`
  - `frontend`: `npm ci`, `npm run typecheck`, `npm test`, `npm run build` (with placeholder `VITE_SUPABASE_*` values), `npm audit --omit=dev --audit-level=high`
  - `migrations`: migration job against a `postgres:15` service container (`.github/workflows/ci.yml`)
- Dependabot / other bots: Not detected

**Local containers:**
- `docker-compose.yml` for development (backend and Vite frontend)

## Environment Configuration

**Required env vars (backend, production):**
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `DATABASE_URL`
- `OPERATOR_USER_IDS`, `VIEWER_USER_IDS`, `CORS_ORIGINS`
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (when `CLOUD_PROVIDER=aws`)
- `GCP_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` (when `CLOUD_PROVIDER=gcp`)
- Optional: `ALERT_WEBHOOK_URL`, `METRICS_TOKEN`

**Required env vars (frontend, build time):**
- `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` (or `VITE_SUPABASE_ANON_KEY`), `VITE_API_BASE_URL`

**Non-secret defaults set in `render.yaml`:**
- `CLOUD_PROVIDER=aws`, `AWS_DEFAULT_REGION=us-east-1`, `ML_MODEL_PATH=/app/models`, `LOG_FORMAT=json`, `FORWARDED_ALLOW_IPS=*`, `GLOBAL_AUTOMATION_ENABLED=false`, `DRY_RUN_MODE=true`, `MAX_ACTIONS_PER_DAY=5`, `MAX_DAILY_SPEND_USD=2.00`, `MAX_MONTHLY_BUDGET_USD=10.00`, `REQUIRE_TWO_PERSON_CONFIG=true`

**Secrets location:**
- Production: Render dashboard environment variables (`sync: false` entries in `render.yaml`)
- Local development: `.env` at repo root (gitignored; template in `.env.example`) and `frontend/.env.local`
- Secret-scanning guard: `.gitleaks.toml`

## Webhooks & Callbacks

**Incoming:**
- No inbound webhooks from third-party services detected. The HTTP API (`backend/app/api/*.py`) is called by the frontend and authenticated by Supabase JWTs.
- Supabase Auth email flows (password reset) redirect back to the frontend `ResetPassword` page; the redirect target is configured in Supabase, not in this repo

**Outgoing:**
- Slack-compatible alert webhook (`ALERT_WEBHOOK_URL`) from `backend/app/services/alerts.py`
- Supabase JWKS fetch from `backend/app/auth.py`
- AWS and GCP control-plane API calls from `backend/app/adapters/aws.py` and `backend/app/adapters/gcp.py`
- Supabase PostgREST calls from `backend/app/db/supabase_client.py`

---

*Integration audit: 2026-10-23*
