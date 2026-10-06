# CloudSentry

Autonomous cloud cost intelligence: discover resources, collect CloudWatch telemetry, detect anomalies (rules, statistics, Isolation Forest), propose optimizations through policies, and execute **safe, reversible** actions behind a kill-switch, dry-run mode, two-person controls and an append-only audit trail.

This repository ships the **backend API, scheduler, ML pipeline, database migrations, IAM policy, Terraform demo, scripts** and a **React dashboard** (`frontend/`).

## How it works

```
discovery ─▶ resources ─▶ telemetry ─▶ anomaly detection ─▶ policy engine ─▶ actions
 (15 min)                  (5 min, CloudWatch)  (rules + z-score/EWMA      (proposals,       (safety layer ─▶ cloud call
                                                 + Isolation Forest)        approval gating)   ─▶ verify ─▶ audit)
```

* **Leader election.** Every replica serves the API; only the holder of a Postgres advisory lock runs the jobs (discovery, pipeline, execute, verify, cost, retrain, retention).
* **Safety layer** (before every cloud mutation): kill-switch, protection tags (`cloudsentry:protected`, `cloudsentry:exempt`, `do-not-stop`), RDS never mutated, daily action cap, separate tag-write cap, per-resource cooldown, daily/monthly budget for spend-raising actions, live-state and anomaly re-checks, proposal expiry.
* **Actions:** `stop_ec2`, `limit_lambda`, `apply_tags` (all reversible), `recommend_review` (no cloud call). Destructive actions are blocked in code and denied in IAM.

## Requirements

- Python 3.11+
- A Supabase project (Postgres + Auth) — `DATABASE_URL` must be the **session pooler (port 5432) or a direct connection**, never the transaction pooler (6543): leader election needs session-level locks. The API refuses to start otherwise.
- AWS credentials for a principal with `cloud_permissions/aws_iam_policy.json` (prefer a role; see `cloud_permissions/README.md`)

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env   # fill real values; never commit .env
python scripts/apply_migrations.py          # applies db/migrations/001..NNN in order
uvicorn backend.app.main:app --reload --port 8000
```

### Frontend

Needs Node.js 20.19+. In a second terminal:

```bash
cd frontend
npm install
cp .env.example .env.local   # VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_KEY (same project as the backend)
npm run dev                  # http://localhost:5173, /api is proxied to :8000
```

Sign in with a Supabase user whose id is in `VIEWER_USER_IDS` / `OPERATOR_USER_IDS` (or that has `app_metadata.cloudsentry_role`). See `frontend/README.md`.

### Database migrations

`db/migrations/NNN_*.sql`, applied in order and recorded in `schema_migrations`. The API **refuses to start** if the database is behind the newest file. Apply with `python scripts/apply_migrations.py` (`--status` to inspect). Databases migrated by hand in the Supabase SQL editor: run the missing files in order there; `011` creates `schema_migrations` and records 001–011. Never edit an applied migration — add a new one.

| File | What it does |
|---|---|
| 001–004 | Schema, indexes, RLS, seed policies |
| 005 | Unique metric samples |
| 006 | Column grants, Realtime, tag-remediation seed |
| 007 | Resource identity, one-active-anomaly / one-in-flight-action indexes, API-only writes |
| 008 | **Append-only `audit_logs`** (UPDATE/TRUNCATE refused, DELETE only after 90 days), request id / client IP |
| 009 | Vocabulary CHECK constraints, explicit grants, role-gated reads (`app_metadata.cloudsentry_role`) |
| 010 | Unused-EBS policy becomes `recommend_review` |
| 011 | `schema_migrations`, persisted job status (`job_runs`) |

## Access control

Every `/api/v1/*` route (except `/health` and `/health/live`) needs `Authorization: Bearer <supabase access token>`.

| Role | How it is granted | Can |
|---|---|---|
| viewer | `VIEWER_USER_IDS` or `app_metadata.cloudsentry_role = "viewer"` | read everything, emergency-stop |
| operator | `OPERATOR_USER_IDS` or `app_metadata.cloudsentry_role = "operator"/"admin"` | + approve/execute/rollback actions, change config, run discovery |

* Plain Supabase sign-up grants **nothing**; anonymous sessions are always rejected. `REQUIRE_VIEWER_ROLE=false` lets any signed-in user read (only if sign-up is closed).
* `app_metadata` is settable only with the service-role key; `user_metadata` never grants roles.
* Direct Supabase reads/Realtime (e.g. a frontend) are gated by migration 009 on `app_metadata.cloudsentry_role`; the `*_USER_IDS` lists apply to the API only.
* **Two-person rule:** enabling `GLOBAL_AUTOMATION_ENABLED` or disabling `DRY_RUN_MODE` returns `202 pending_confirmation`; a *different* operator must repeat the same change within an hour. An emergency stop cancels a pending enable.

## API

| Method | Path | Role |
|---|---|---|
| GET | `/api/v1/health/live` | public (liveness, no DB) |
| GET | `/api/v1/health` | public (readiness: `{status, db}` only, cached 5 s) |
| GET | `/api/v1/system/health` | viewer (jobs, models, budget, issues) |
| GET | `/api/v1/system/config` | viewer |
| PATCH | `/api/v1/system/config` · `/api/v1/system/{key}` | operator (two-person for unsafe changes) |
| POST | `/api/v1/system/emergency-stop` | viewer (always applied; rate-limited) |
| GET | `/api/v1/resources/` · `/{id}` | viewer |
| POST | `/api/v1/resources/discover` | operator (2/min, one at a time) |
| GET | `/api/v1/metrics/{id}` · `/{id}/summary` | viewer |
| GET | `/api/v1/anomalies/` · `/{id}` | viewer |
| PATCH | `/api/v1/anomalies/{id}` · `/{id}/status` | operator |
| GET | `/api/v1/actions/` · `/{id}` | viewer |
| POST | `/api/v1/actions/{id}/approve` · `/execute` · `/rollback` | operator |
| GET | `/api/v1/dashboard/summary` · `/cost-trend` · `/anomaly-summary` | viewer |
| GET | `/api/v1/audit-logs/` (filters: `event_type`, `actor`, `request_id`, `since`, `until`, `resource_id`, `action_id`) | viewer |
| GET | `/metrics` | Prometheus text; only when `METRICS_TOKEN` is set, with `Authorization: Bearer <METRICS_TOKEN>` |

`/docs` and `/openapi.json` are off unless `EXPOSE_API_DOCS=true` (on in `docker-compose.yml` for local dev). Every response carries `X-Request-ID`; audit rows record it with the client IP.

## Configuration

All settings are environment variables (see `.env.example` for the full, commented list). The most important:

| Variable | Default | Meaning |
|---|---|---|
| `GLOBAL_AUTOMATION_ENABLED` | `false` | Kill-switch (runtime value in `system_config` wins) |
| `DRY_RUN_MODE` | `true` | Log "WOULD act"; no mutating cloud calls |
| `MAX_ACTIONS_PER_DAY` / `ACTION_COOLDOWN_MINUTES` | `5` / `30` | State-change caps (bounded; `nan`/`inf` rejected) |
| `MAX_TAG_ACTIONS_PER_DAY` | `50` | Separate cap for tag writes |
| `MAX_DAILY_SPEND_USD` / `MAX_MONTHLY_BUDGET_USD` | `2` / `10` | Budget gate for spend-raising actions |
| `APPROVAL_TTL_HOURS` | `72` | Older proposals/approvals expire instead of executing |
| `REQUIRE_TWO_PERSON_CONFIG` | `true` | Second operator confirms unsafe config changes |
| `OPERATOR_USER_IDS` / `VIEWER_USER_IDS` | — | Role allowlists (comma-separated Supabase user ids) |
| `AWS_REGIONS` | default region | Extra regions to discover/monitor (IAM policy must allow them) |
| `RATE_LIMIT_PER_MINUTE` | `300` | Per-client-IP limit, per process |
| `ALERT_WEBHOOK_URL` | — | Slack-compatible webhook: failed actions, verification timeouts, rollbacks, emergency stop, failing jobs |
| `METRICS_TOKEN` | — | Enables `/metrics` |
| `AUDIT_RETENTION_DAYS` / `HISTORY_RETENTION_DAYS` | `365` | Retention (minimum 90) |
| `ML_MODEL_PATH` | `<repo>/models` | Must be persistent (Render: the disk in `render.yaml`) |

## ML

* **Cold start:** z-score (< `ML_MIN_POINTS_ZSCORE` samples), then EWMA, then Isolation Forest once a resource has `ML_MIN_POINTS_IF` samples (≈7 days at 5 min) **and** a model is trained.
* **Training:** nightly (`JOB_RETRAIN_HOUR_UTC`), per resource type, up to `ML_MAX_TRAINING_ROWS` recent samples, `ML_N_JOBS` threads.
* **Promotion gate:** a new model is validated on the most recent 20 % of the data and promoted only if it flags ≤ `ML_MAX_HOLDOUT_FLAG_RATE` of it; otherwise the previous model stays.
* **Metadata:** `if_<type>_latest.json` next to each model (version, trained_at, sample count, holdout flag rate, feature drift vs the previous model, scikit-learn version). Anomalies record the real model version; models from a different scikit-learn version are not loaded. `/system/health` reports model age (`model_stale:<type>` after `ML_MODEL_STALE_HOURS`) and drift.
* **Offline evaluation:** `python -m ml.evaluation --strict` (deterministic; runs in CI).
* Anomalies clear themselves: rule anomalies when the condition clears, behavioural ones after 30 quiet minutes, and all of them when the resource is stopped/terminated.

## Cloud providers

| | AWS | GCP |
|---|---|---|
| Discovery | EC2, Lambda, S3, RDS, EBS; all `AWS_REGIONS` | Compute instances, all zones of `GCP_PROJECT_ID` (ids are `<zone>/<name>`) |
| Telemetry | CloudWatch, per region | **not implemented** (no metric-based detection) |
| Actions | stop/start EC2, Lambda concurrency, tags (+ rollback) | stop/start VMs with live-state verification |
| Tags/labels | yes | not yet |
| Pricing | `data/pricing/aws_<region>.json` (us-east-1 fallback) | — |

## Observability

* JSON logs (`LOG_FORMAT=json`) with `request_id` and any `extra=` fields; credentials (DSN passwords, bearer tokens, JWTs, AWS keys) are scrubbed from messages and tracebacks.
* `/metrics` (Prometheus text): HTTP requests by route template and status, durations, action outcomes, alert deliveries, job runs, consecutive job failures.
* Alerts via `ALERT_WEBHOOK_URL` (https only), de-duplicated per event for `ALERT_MIN_INTERVAL_SECONDS`.

## Deployment

* **Docker:** `docker compose up --build` (local dev: backend on :8000 and frontend on :5173 with hot reload, docs on, bound to 127.0.0.1; the frontend reads `frontend/.env.local`). The image runs as a non-root user, has a liveness `HEALTHCHECK`, and includes `scripts/apply_migrations.py`.
* **Render:** `render.yaml` runs migrations as a pre-deploy step, mounts a persistent disk for models, lists every required variable, and defines the `cloudsentry-frontend` static site (set `VITE_API_BASE_URL` to the backend URL and add the site's origin to `CORS_ORIGINS`). Keep `GLOBAL_AUTOMATION_ENABLED=false` and `DRY_RUN_MODE=true` until the kill-switch and rollback have been rehearsed.
* **Before deploying a new release:** apply migrations (the API refuses an older schema), re-attach `cloud_permissions/aws_iam_policy.json` if it changed, and set `OPERATOR_USER_IDS` / `VIEWER_USER_IDS` (or app_metadata roles) — users without a role get 403.

## Demo environment

Use a sandbox AWS account.

* **Terraform** (`terraform/`): an idle instance, a protected instance, an orphaned volume, a bucket and a Lambda; optionally the CloudSentry IAM role (`-var create_cloudsentry_role=true`).
* **Live telemetry without AWS:** `scripts/telemetry_demo.py start` (requires `CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS=1`) seeds four protected demo resources (2 EC2, Lambda, RDS), backfills 6 h of history and writes a sample every 10 s. Change behaviour while it runs: type `web spike` / `all idle` into its terminal, or run `scripts/telemetry_demo.py set <web|worker|thumbnailer|db|all> <normal|busy|spike|idle|errors|off>` from another one. Charts on a resource page refresh every 10 s; anomalies follow on the next pipeline run (`JOB_PIPELINE_MINUTES=1`, `ML_IDLE_WINDOW_HOURS=2` for a quick demo). `cleanup` removes it all.
* **Scripts:** `scripts/provision_demo.sh` (needs `DEMO_LAMBDA_ROLE_ARN` on real AWS, e.g. `terraform output demo_lambda_role_arn`), `scripts/shutdown_demo.sh [--terminate]`, `scripts/run_stress.sh` (mock endpoints only), `scripts/inject_anomaly.py` (requires `CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS=1`; `--cleanup <resource_id>` removes the samples).

## Tests

```bash
pytest                                   # unit + integration (moto, in-memory DB fake)
python -m ml.evaluation --strict         # ML targets
```

CI (`.github/workflows/ci.yml`) also runs ruff, gitleaks, pip-audit, coverage (≥ 70 %), Python 3.11 and 3.12, all migrations against a real Postgres (including the append-only audit and vocabulary checks), and a Docker build.
