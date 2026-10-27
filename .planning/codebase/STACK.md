---
last_mapped_commit: 7c29211f4ff4cdfd1dad8c9001b1fadd2b22c34f
last_mapped_at: 2026-10-23
---
# Technology Stack

**Analysis Date:** 2026-10-23

## Languages

**Primary:**
- Python 3.11 (Docker image and CI target; CI matrix also tests 3.12) - all backend code in `backend/app/`, ML code in `ml/`, scripts in `scripts/`, tests in `tests/`
- TypeScript (strict, `tsc --noEmit`) - React dashboard in `frontend/src/`

**Secondary:**
- SQL (PostgreSQL dialect) - schema migrations in `db/migrations/001_initial_schema.sql` through `015_fail_timed_out_action.sql`
- HCL (Terraform) - demo AWS fixtures in `terraform/main.tf`, `terraform/iam.tf`, `terraform/variables.tf`, `terraform/outputs.tf`
- YAML - Render blueprint `render.yaml`, GitHub Actions `.github/workflows/ci.yml`, Docker Compose `docker-compose.yml`, GCP custom role `cloud_permissions/gcp_custom_role.yaml`
- JSON - synthetic ML scenarios in `ml/synthetic/scenarios/*.json`

## Runtime

**Backend Environment:**
- CPython 3.11 (`FROM python:3.11-slim` in `backend/Dockerfile`), `target-version = "py311"` in `ruff.toml`
- Runs as non-root user `appuser` (uid 1000) in the container

**Frontend Environment:**
- Node.js >= 20.19 (declared `engines` in `frontend/package.json`); CI and Docker Compose use Node 22 (`node:22-alpine`, `node-version: "22"` in `.github/workflows/ci.yml`)

**Package Managers:**
- pip with lower-bounded, major-capped requirements in `backend/requirements.txt` and `backend/requirements-dev.txt` (dev file includes runtime file via `-r`)
  - Lockfile: missing for Python (no pinned lock; bounds only)
- npm
  - Lockfile: present (`frontend/package-lock.json`); CI uses `npm ci`

## Frameworks

**Backend Core:**
- FastAPI `>=0.111.0,<1` - HTTP API; app object `backend.app.main:app`
- Uvicorn `>=0.30.0,<1` - ASGI server (run with `--proxy-headers`)
- Pydantic `>=2.0.0,<3` and pydantic-settings `>=2.0.0,<3` - schemas in `backend/app/schemas/models.py`; settings in `backend/app/config.py`
- APScheduler `>=3.10.0,<4` - background jobs via `AsyncIOScheduler` in `backend/app/scheduler.py`

**Frontend Core:**
- React `^19.3.0` and react-dom - UI (`frontend/src/main.tsx`, `frontend/src/App.tsx`)
- react-router-dom `^7.18.4` - routing
- @tanstack/react-query `^5.104.1` - server state (`frontend/src/hooks/queries.ts`)
- Recharts `^3.10.1` - charts (`frontend/src/components/charts.tsx`)
- Radix UI (`@radix-ui/react-dialog`, `react-dropdown-menu`, `react-tooltip`) - accessible primitives (`frontend/src/components/Dialog.tsx`)
- @phosphor-icons/react `^2.1.10` - icons
- Fontsource variable fonts: Geist, Geist Mono, Newsreader

**ML:**
- scikit-learn `>=1.4.0,<2` - `IsolationForest` anomaly model (`ml/trainer.py`), metrics (`ml/evaluation.py`)
- pandas `>=2.2.0,<4`, numpy `>=1.26.0,<3`, scipy `>=1.13.0,<2`, joblib `>=1.4.0,<2` - feature engineering (`ml/features.py`), model serialization (`ml/inference.py` loads with `joblib.load`)

**Build/Dev (Frontend):**
- Vite `^8.3.2` - dev server and bundler (`frontend/vite.config.ts`); scripts `dev`, `dev:demo` (mode `demo`), `build`, `preview`
- @vitejs/plugin-react `^6.1.1`
- Tailwind CSS `^4.3.3` with `@tailwindcss/vite`
- TypeScript `^7.0.2` - `typecheck` script

**Testing:**
- pytest `>=8.0.0,<10` with pytest-asyncio `>=0.23.0,<2` (`asyncio_mode = auto` in `pytest.ini`), pytest-cov `>=5.0.0,<8`, pytest-mock `>=3.14.0,<4`
- moto `[ec2,cloudwatch,s3,lambda,rds]>=5.0.0,<6` - AWS mocking
- Vitest `^5.0.3` with jsdom `^29.1.1` and @testing-library/react `^16.3.3`, jest-dom, user-event (`frontend/src/test/`)

**Lint / Security Tooling:**
- ruff `>=0.6.0,<1` - configured in `ruff.toml`, restricted to real-bug rules (`E9`, `F63`, `F7`, `F82`), line length 120
- pip-audit `>=2.7.0,<3` - run with `--strict` against `backend/requirements.txt` in CI
- gitleaks - secret scanning via `gitleaks/gitleaks-action@v2`; config `.gitleaks.toml`
- npm audit - `npm audit --omit=dev --audit-level=high` in CI

## Key Dependencies

**Critical (backend):**
- `supabase` `>=2.7.0,<3` - service-role client for PostgREST data access (`backend/app/db/supabase_client.py`)
- `asyncpg` `>=0.29.0,<1` - direct PostgreSQL pool for time-series queries (`backend/app/db/asyncpg_pool.py`, max_size 5, command_timeout 60s); no libpq/psycopg
- `PyJWT[crypto]` `>=2.10,<3` - JWT verification of Supabase tokens (`backend/app/auth.py`); chosen over python-jose per `requirements.txt` comment
- `httpx` `>=0.27.0,<1` - JWKS fetch (`backend/app/auth.py`) and alert webhook POST (`backend/app/services/alerts.py`)
- `boto3` / `botocore` `>=1.34.0,<2` - AWS EC2, Lambda, S3, RDS, CloudWatch clients (`backend/app/adapters/aws.py`)
- `google-cloud-compute` `>=1.15.0,<2`, `google-cloud-monitoring` `>=2.14.0,<3`, `google-cloud-resource-manager` `>=1.10.0,<2` - GCP adapter (`backend/app/adapters/gcp.py`)

**Critical (frontend):**
- `@supabase/supabase-js` `^2.117.2` - browser auth (`frontend/src/lib/supabase.ts`, `frontend/src/auth/AuthProvider.tsx`)

**Infrastructure:**
- `python-dotenv` `>=1.0.0,<2` - local `.env` loading
- `uvicorn[standard]` not used; plain `uvicorn` only
- Structured JSON logging via `backend/app/logging_config.py` (`LOG_FORMAT=json`)
- In-process sliding-window rate limiter `backend/app/rate_limit.py` (no Redis)
- Prometheus-style metrics served by `backend/app/metrics.py` (in-process counters, no client library)

## Configuration

**Environment:**
- Settings loaded by pydantic-settings class `Settings` in `backend/app/config.py`; the complete variable list is in `.env.example` (categories: Supabase, database, AWS, GCP, logging, scheduler, CORS, rate limit, ML thresholds, safety/budget limits, retention, environment)
- Local env file `.env` at repo root (read by `docker-compose.yml` via `env_file`); `.env` is gitignored. Its contents are intentionally not documented here.
- Frontend uses Vite env vars: `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` or `VITE_SUPABASE_ANON_KEY` (`frontend/src/lib/env.ts`), `VITE_API_BASE_URL` (`frontend/src/lib/env.ts`, `frontend/vite.config.ts`), `VITE_DEMO` (`frontend/src/lib/env.ts`, `frontend/src/lib/demo.ts`), `VITE_DEV_API_PROXY` (dev proxy target in `frontend/vite.config.ts`)
- Frontend local config expected at `frontend/.env.local` (referenced in `docker-compose.yml`)
- Settings-level defaults: `CLOUD_PROVIDER=aws` (`'aws'` or `'gcp'`, selected by `backend/app/adapters/factory.py`), `AWS_DEFAULT_REGION=us-east-1`, `SCHEDULER_ENABLED`, `DRY_RUN_MODE`, `GLOBAL_AUTOMATION_ENABLED`, `REQUIRE_TWO_PERSON_CONFIG`
- Runtime-adjustable settings are stored and read through `backend/app/services/runtime_config.py`

**Database Migrations:**
- Applied by `scripts/apply_migrations.py` (also used as Render `preDeployCommand`). The API refuses to start if `schema_migrations` max version is behind (see `db/migrations/011_schema_version_and_job_runs.sql`).

**Build:**
- `backend/Dockerfile` - single-stage `python:3.11-slim`, installs `backend/requirements.txt` only (no compiler, no libpq), copies `backend/`, `ml/`, `data/`, `db/`, `scripts/apply_migrations.py`; creates `/app/models`; `HEALTHCHECK` on `/api/v1/health/live`
- `frontend/vite.config.ts` - Vite build to `frontend/dist`; `tsconfig.json` for TypeScript
- `docker-compose.yml` - local dev only: backend with `--reload` and source mounts, frontend on `node:22-alpine` running `npm ci && npm run dev`
- `render.yaml` - production blueprint (see below)
- `.dockerignore` at repo root

## Platform Requirements

**Development:**
- Python 3.11+ with `backend/requirements-dev.txt` installed (or Docker)
- Node.js >= 20.19 (22 recommended) for `frontend/`
- Access to a Supabase project (URL, service role key, JWT secret) and a PostgreSQL DSN (`DATABASE_URL`, session pooler on 5432 or direct; not 6543 per `render.yaml`)
- Optional AWS credentials and/or GCP credentials (`GOOGLE_APPLICATION_CREDENTIALS`) for live discovery
- Terraform (`hashicorp/aws ~> 5.0` provider, `terraform/main.tf`) to provision demo resources

**Production:**
- Render.com
  - Backend: web service `cloudsentry-backend`, Docker runtime, `plan: starter`, region `ohio`, health check `/api/v1/health`, 1 GB persistent disk `cloudsentry-models` mounted at `/app/models` (forces single instance)
  - Frontend: static site `cloudsentry-frontend`, `rootDir: frontend`, `npm ci && npm run build`, publish `dist`, rewrite `/*` to `/index.html`, security headers (X-Frame-Options DENY, Referrer-Policy no-referrer, X-Content-Type-Options nosniff)
- Supabase hosted project - Postgres, Auth, PostgREST
- AWS us-east-1 by default (target account of discovery and actions)

---

*Stack analysis: 2026-10-23*
