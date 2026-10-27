---
last_mapped_commit: 7c29211f4ff4cdfd1dad8c9001b1fadd2b22c34f
last_mapped_at: 2026-10-23
---
<!-- refreshed: 2026-10-23 -->

# Architecture

**Analysis Date:** 2026-10-23

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│  React SPA (Vite)  `frontend/src/`                                    │
│  pages/* · hooks/queries.ts · lib/api.ts · auth/AuthProvider.tsx      │
└──────────────┬───────────────────────────────┬───────────────────────┘
               │ Bearer JWT (REST, JSON)       │ Supabase Auth (login, refresh)
               ▼                               ▼
┌──────────────────────────────┐   ┌────────────────────────────────────┐
│ FastAPI app                  │   │ Supabase (Auth, Postgres)          │
│ `backend/app/main.py`        │   │ `db/migrations/*.sql`              │
│  ├─ middleware stack         │   └────────────────────────────────────┘
│  ├─ routers `backend/app/api`│                    ▲
│  └─ lifespan → scheduler     │ asyncpg pool / supabase-py client
└──────────────┬───────────────┘                    │
               │ in-process calls                   │
               ▼                                    │
┌──────────────────────────────────────────────────────────────────────┐
│ Service layer  `backend/app/services/`                                │
│  discovery → telemetry → anomaly_detector → policy_engine             │
│  action_runner ── safety_layer · two_person · audit_logger            │
│  cost_estimator · ml_retrainer · retention · alerts · runtime_config  │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Cloud adapters  `backend/app/adapters/` (CloudAdapter ABC)            │
│  AWSAdapter `aws.py` · GCPAdapter `gcp.py` · factory.py selects one   │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
        AWS / GCP APIs (discovery, CloudWatch/Monitoring, mutations)

ML side (offline + inference): `ml/` (features, baseline, trainer, inference)
loaded by `backend/app/services/anomaly_detector.py` and `ml_retrainer.py`.
```

## Pattern Overview

**Overall:** Modular monolith. A single FastAPI process serves the REST API and also runs background jobs (APScheduler) in-process. Business logic lives in `backend/app/services/`, which is called by both HTTP routers and scheduled jobs. Cloud provider access is isolated behind an abstract adapter interface. The frontend is a separate SPA that talks only to the REST API (plus Supabase Auth for sign-in).

**Key Characteristics:**
- One deployable backend image (`backend/Dockerfile` runs `uvicorn backend.app.main:app`); the same process is API and scheduler host.
- Multi-replica safety via Postgres session advisory locks: only the leader runs leader-only jobs (`backend/app/db/leader.py`, `backend/app/scheduler.py` `LEADER_ONLY_JOBS`).
- Human-in-the-loop action model: detections become `actions` rows that go through approve / execute / verify / rollback, gated by `safety_layer.py` and `two_person.py`.
- Provider selection is a runtime setting (`CLOUD_PROVIDER` in `backend/app/config.py`), not a compile-time choice.
- Fail-closed startup: `validate_startup_config()` in `backend/app/main.py` refuses to boot without JWT verification source, Supabase credentials, or `DATABASE_URL`, and rejects transaction-pooler URLs (session-level locks would break leader election).
- Two data-access paths coexist: raw asyncpg pool (`backend/app/db/asyncpg_pool.py`) and the Supabase Python client (`backend/app/db/supabase_client.py`, `get_supabase_client()`).

## Layers

**HTTP / API layer:**
- Purpose: Request validation, authN/authZ, response shaping, pagination headers.
- Location: `backend/app/api/`
- Contains: One router module per resource (`actions.py`, `anomalies.py`, `audit.py`, `dashboard.py`, `metrics.py`, `policies.py`, `resources.py`, `system.py`).
- Depends on: `backend/app/auth.py`, `backend/app/services/*`, `backend/app/schemas/models.py`, `backend/app/db/pagination.py`.
- Used by: The frontend SPA via `frontend/src/lib/api.ts`.

**Middleware / cross-cutting:**
- Purpose: Metrics, security headers, rate limiting, request IDs, CORS.
- Location: `backend/app/main.py` (inline middlewares), `backend/app/rate_limit.py` (`RateLimitMiddleware`, `SlidingWindowLimiter`), `backend/app/request_context.py` (`RequestContextMiddleware`), `backend/app/metrics.py`.
- Order: `RequestContextMiddleware` is added last so it is outermost (every response, including 429s, carries a request id). Then CORS, then `RateLimitMiddleware`; `request_metrics` and `security_headers` are `@app.middleware("http")` decorators.

**Auth layer:**
- Purpose: Verify Supabase-issued JWTs (HS256 secret, ES256/JWKS), map roles to viewer/operator.
- Location: `backend/app/auth.py`
- Key functions: `verify_token`, `get_current_user`, `require_viewer`, `require_operator`, `is_operator`, `actor_label`.
- Used by: Every router is mounted with `dependencies=[Depends(require_viewer)]`; mutating endpoints add `require_operator` per route.

**Service / domain layer:**
- Purpose: All business rules: discovery, telemetry ingestion, anomaly scoring, policy evaluation, action execution and rollback, cost estimation, retention.
- Location: `backend/app/services/`
- Contains: `discovery.py` (`DiscoveryService`), `telemetry.py` (`TelemetryService`), `anomaly_detector.py` (`AnomalyDetectorService`), `policy_engine.py` (`PolicyEngine`), `action_runner.py` (`ActionRunner`), `safety_layer.py` (`SafetyLayer`), `two_person.py`, `audit_logger.py`, `cost_estimator.py` (`CostEstimationService`), `ml_retrainer.py` (`MLRetrainingService`), `retention.py` (`RetentionService`), `alerts.py`, `health.py`, `job_status.py`, `runtime_config.py`, `telemetry.py`.
- Depends on: `backend/app/adapters/`, `backend/app/db/`, `ml/`.

**Adapter layer (cloud boundary):**
- Purpose: Normalize AWS and GCP discovery, metrics, and mutations behind one interface.
- Location: `backend/app/adapters/`
- Contract: `CloudAdapter` in `backend/app/adapters/base.py` defines `discover_instances/functions/buckets/databases/volumes`, `get_metric_data`, `stop_instance`, `start_instance`, `limit_function_concurrency`, `remove_function_concurrency`, `apply_tags`, and optional `get_tags`. Errors surface as `CloudProviderError` (distinct from an empty result).
- Selection: `get_cloud_adapter(region)` in `backend/app/adapters/factory.py` dispatches on `settings.CLOUD_PROVIDER`; `AWSAdapter` takes a region, `GCPAdapter` is project-wide.

**Persistence layer:**
- Purpose: Connection management and schema versioning.
- Location: `backend/app/db/` (`asyncpg_pool.py`, `supabase_client.py`, `leader.py`, `schema.py`, `pagination.py`), SQL in `db/migrations/`.
- Schema gate: `check_schema_version()` in `backend/app/db/schema.py` runs at startup (skipped when `CLOUDSENTRY_TESTING=1`).

**ML layer:**
- Purpose: Feature extraction, IsolationForest-style scoring with a decision threshold, baseline heuristics, offline training and evaluation.
- Location: `ml/` (`features.py`, `inference.py`, `baseline.py`, `trainer.py`, `evaluation.py`, `synthetic/generator.py`).
- Runtime use: `backend/app/services/anomaly_detector.py` calls into `ml/inference.py`; retraining is scheduled through `backend/app/services/ml_retrainer.py`.

**Frontend:**
- Purpose: Operator/viewer dashboard.
- Location: `frontend/src/`
- Structure: `App.tsx` (route table, lazy-loaded pages) → `components/Layout.tsx` → `pages/*.tsx`; data via TanStack-style hooks in `hooks/queries.ts`; HTTP via `lib/api.ts`; auth via `auth/AuthProvider.tsx` and `lib/supabase.ts`; role gating in `lib/role.ts`.
- Demo mode: `lib/demo.ts` and `frontend/.env.demo` provide a non-networked data path.

## Data Flow

### Primary Detection-to-Action Path (scheduled)

1. `discovery_job` (interval, leader-only) calls `DiscoveryService.run()`, which calls every adapter's `discover_*` and syncs rows into resource tables (`backend/app/services/discovery.py`, `_sync_resources`).
2. `pipeline_job` (interval, leader-only) in `backend/app/scheduler.py` runs in strict order: `TelemetryService.run()` → `AnomalyDetectorService.run()` → `PolicyEngine.evaluate_all()`. Detection always sees the samples just collected.
3. `PolicyEngine` (`backend/app/services/policy_engine.py`) reads rows from `policies` and proposes `actions` (recommendations).
4. `action_execute_job` calls `ActionRunner.execute_pending_auto()`; `action_verify_job` calls `ActionRunner.verify_pending()`. Both run in a thread pool (`_run_sync` in `scheduler.py`).
5. `ActionRunner.execute_action` (`backend/app/services/action_runner.py`) claims the row, re-checks live state, consults `SafetyLayer`, calls the adapter mutation, then `_finish` writes the outcome and an audit row. Verification later moves actions to completed (`_complete_verified`) or failed (`_fail_timed_out`).

### Human-Initiated Action Path (HTTP)

1. SPA calls `POST /api/v1/actions/{id}/approve` with `Authorization: Bearer <jwt>` (`frontend/src/lib/api.ts`).
2. `RateLimitMiddleware` and `RequestContextMiddleware` run; `require_viewer` then `require_operator` resolve the user (`backend/app/auth.py`).
3. `backend/app/api/actions.py` `approve_action` delegates to services; `execute` and `rollback` return `202` and enqueue work through FastAPI `BackgroundTasks` using `get_runner()`.
4. Two-person controls for sensitive toggles go through `two_person.record_request` / `live_request` (`backend/app/services/two_person.py`), e.g. enabling automation requires two operators (covered by `tests/test_auth.py`).

### Read Path (dashboard)

1. Page component (e.g. `frontend/src/pages/Dashboard.tsx`) calls a query hook in `frontend/src/hooks/queries.ts`.
2. Hook calls `/api/v1/dashboard/*` or `/api/v1/resources`, `/api/v1/anomalies`, `/api/v1/audit-logs`.
3. Router queries Postgres through `asyncpg` pool (`get_pool()`) or the Supabase client; list endpoints set `TOTAL_COUNT_HEADER` via `backend/app/db/pagination.py`.

**State Management:**
- Backend: Durable state lives in Postgres. In-process state is limited to `scheduler._state` (leader flag), `scheduler._services` (lazily built singletons), `runtime_config` (values refreshed from DB every minute by `config_refresh_job`), `two_person` requests, and rate-limit counters.
- Frontend: Server state via query hooks; URL state via `hooks/useUrlState.ts`; auth session via `AuthProvider`.

## Key Abstractions

**CloudAdapter:**
- Purpose: Single interface over AWS and GCP.
- Examples: `backend/app/adapters/base.py`, `backend/app/adapters/aws.py`, `backend/app/adapters/gcp.py`
- Pattern: Abstract base class plus factory function.

**ActionRunner / Action lifecycle:**
- Purpose: Owns the state machine for proposed, approved, executing, verifying, completed, failed, expired, and rolled-back actions.
- Examples: `backend/app/services/action_runner.py`, migrations `db/migrations/014_complete_verified_action.sql`, `015_fail_timed_out_action.sql`
- Pattern: Claim-then-act with compare-and-set transitions in SQL; every transition audited.

**SafetyLayer:**
- Purpose: Vetoes unsafe mutations (protected resources, synthetic resources, dry-run and emergency-stop modes).
- Examples: `backend/app/services/safety_layer.py` (`is_synthetic`, `SafetyLayer`), `tests/test_safety_layer.py`, `tests/test_resource_protection.py`.

**Leader election:**
- Purpose: Ensure one replica runs singleton jobs.
- Examples: `backend/app/db/leader.py` (`try_acquire`, `release`, `is_leader`), `scheduler.job_leadership` (every 30 s).
- Pattern: Postgres session advisory lock; requires a non-transaction-pooler DB connection.

**Pydantic schemas:**
- Purpose: Request and response contracts shared by routers.
- Examples: `backend/app/schemas/models.py` (`Action`, and others).

## Entry Points

**HTTP API:**
- Location: `backend/app/main.py` (`app = FastAPI(...)`)
- Triggers: uvicorn (`backend/Dockerfile`), `render.yaml` deploy, `docker-compose.yml` locally.
- Responsibilities: Mounts routers under `/api/v1/*`, `/metrics` (404 unless `METRICS_TOKEN` set), liveness `/api/v1/health/live`, readiness `/api/v1/health`.

**Scheduler:**
- Location: `backend/app/scheduler.py` (`setup_scheduler`, `start`, `shutdown`)
- Triggers: Started from the FastAPI lifespan when `SCHEDULER_ENABLED` is true and not in testing mode.
- Responsibilities: Discovery, pipeline, action execute/verify, cost estimation (cron minute 5), ML retraining (cron, `JOB_RETRAIN_HOUR_UTC`), retention (cron, `JOB_RETENTION_HOUR_UTC`), config refresh, leadership.

**Frontend:**
- Location: `frontend/src/main.tsx` → `frontend/src/App.tsx`
- Triggers: Browser load; routes `/login`, `/reset-password`, and authenticated routes behind `Layout`.

**Operational scripts:**
- Location: `scripts/apply_migrations.py` (applies `db/migrations/*.sql`), `scripts/provision_demo.sh`, `scripts/inject_anomaly.py`, `scripts/telemetry_demo.py`, `scripts/test_supabase.py`.
- Triggers: Manual, not part of the service runtime.

## Architectural Constraints

- **Threading:** Async FastAPI event loop plus `ThreadPoolExecutor(max_workers=4)` (`cloudsentry-job`) in `backend/app/scheduler.py` for blocking boto3/GCP and DB work. Routes that call blocking code directly will stall the loop; use `run_in_threadpool` (as `main.py` does for `runtime_config.refresh_from_db`).
- **Global state:** Module-level singletons: `scheduler.scheduler` (AsyncIOScheduler), `scheduler._services`, `scheduler._state`, `backend/app/metrics.py` registry, `backend/app/services/runtime_config.py` settings cache, `backend/app/services/two_person.py` request store, `backend/app/auth.py` JWKS cache (`_get_jwks`), `backend/app/db/asyncpg_pool.py` pool.
- **Single-process assumption for counters:** Rate-limit and metrics state are per process; multi-replica deployments see per-replica values.
- **Circular imports:** Scheduler imports services lazily inside `_svc()` to avoid import-time failures and cycles. Keep that pattern for new services.
- **Pooler constraint:** `DATABASE_URL` must not use the transaction pooler (port 6543 / pgbouncer). Leader election depends on session locks.
- **Layering rule in practice:** Routers should call services, not adapters directly. Verify with `rg "adapters" backend/app/api` before adding new router code.

## Anti-Patterns

### Scheduler and API share one event loop for some work

**What happens:** `job_discovery`, `job_execute`, and `job_verify` push blocking work to the thread pool, but `job_pipeline` calls `TelemetryService.run()` and `AnomalyDetectorService.run()` directly with `await`, so their internal blocking calls run on the loop.
**Why it's wrong:** Long CloudWatch or model calls during that job can delay HTTP responses served by the same process.
**Do this instead:** Wrap the synchronous `run()` methods in `_run_sync(...)` the way `policy_engine` is wrapped in `job_pipeline` (`backend/app/scheduler.py`).

### Documented architecture diverges from implementation

**What happens:** `CHANGES.md` flags that the documented architecture and the running architecture differ (`GCPAdapter` and `factory.py` coverage).
**Why it's wrong:** Planners reading older design docs may target components that do not exist or are not wired.
**Do this instead:** Treat the code in `backend/app/` and this file as authoritative; check `backend/app/adapters/factory.py` for the actually supported providers.

### Health check does not reflect dependencies for orchestrators

**What happens:** `/api/v1/health/live` never checks dependencies by design; `/api/v1/health` is the readiness path.
**Why it's wrong:** Using the liveness path for load-balancer decisions hides DB outages.
**Do this instead:** Point readiness probes at `/api/v1/health` (`backend/app/services/health.py`), and keep liveness dependency-free (as `backend/Dockerfile` HEALTHCHECK does).

## Error Handling

**Strategy:** Fail closed at startup, return generic 500s at runtime, and record job failures.

**Patterns:**
- Global `Exception` handler in `backend/app/main.py` logs the stack and returns `{"detail": "Internal server error"}`; cloud and Postgres error text is never returned to clients.
- Adapters raise `CloudProviderError`; discovery raises `DiscoveryError` carrying a summary dict (`backend/app/services/discovery.py`).
- Scheduled jobs are wrapped by `_tracked` in `backend/app/scheduler.py`, which records status (`job_status.py`), persists it, increments metrics, and calls `alerts.job_failing` (webhook must be `https://`, see `validate_startup_config`).
- Auth failures return 401 with `WWW-Authenticate`; `/metrics` returns 404 when disabled to avoid exposure.

## Cross-Cutting Concerns

**Logging:** JSON or text formatters with secret scrubbing and a request-id filter in `backend/app/logging_config.py` (`SecretScrubFilter`, `RequestIdFilter`), configured once in `main.py`.
**Validation:** Pydantic models in `backend/app/schemas/models.py` and FastAPI parameter validation on routers; SQL constraints in `db/migrations/005_constraints.sql` and `009_grants_and_constraints.sql`.
**Authentication:** Supabase JWT verification in `backend/app/auth.py`; Row Level Security policies in `db/migrations/003_rls_policies.sql` and `007_integrity_and_rls.sql`.
**Audit:** `backend/app/services/audit_logger.py` writes to the audit table exposed at `/api/v1/audit-logs`; integrity enforced by `db/migrations/008_audit_integrity.sql`.
**Metrics:** Prometheus text exposition at `/metrics` (`backend/app/metrics.py`), labels bounded by route template (`_route_template` in `main.py`).

---

*Architecture analysis: 2026-10-23*
