# CloudSentry

Autonomous AWS cost intelligence: discover resources, collect CloudWatch telemetry, detect anomalies, and execute **safe, reversible** optimizations behind a kill-switch and dry-run mode.

This repository currently ships the **backend, ML pipeline, database migrations, and AWS demo scripts**. There is no frontend in git.

## Requirements

- Python 3.11
- A Supabase project (Postgres + Auth)
- An AWS IAM user using `cloud_permissions/aws_iam_policy.json`
- `SUPABASE_JWT_SECRET` set to the project JWT secret (Dashboard → Settings → API)

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env   # fill real values; never commit .env
```

Apply database migrations in the Supabase SQL editor (in order):

- `db/migrations/001_initial_schema.sql`
- `db/migrations/002_indexes.sql`
- `db/migrations/003_rls_policies.sql`
- `db/migrations/004_seed_policies.sql`
- `db/migrations/005_constraints.sql`
- `db/migrations/006_security_and_seed.sql`

Run the API:

```bash
uvicorn backend.app.main:app --reload --port 8000
```

Health (public): `GET http://localhost:8000/api/v1/health`  
All other `/api/v1/*` routes require `Authorization: Bearer <supabase_access_token>`.

## Docker

```bash
docker compose up --build
```

The image is built from the **repository root** (`backend/Dockerfile` copies `backend/requirements.txt`, `backend/`, `ml/`, and `data/`). Models are expected at `/app/models`.

## Safety defaults

| Flag | Default | Meaning |
|---|---|---|
| `GLOBAL_AUTOMATION_ENABLED` | `false` | Kill-switch. Runtime value is `system_config` overlaid on env. |
| `DRY_RUN_MODE` | `true` | Logs WOULD-act; no mutating AWS calls. |
| `LAMBDA_CONCURRENCY_LIMIT` | `10` | Cap used instead of concurrency `0`. |

Emergency stop (authenticated): `POST /api/v1/system/emergency-stop`

Do **not** enable automation on a production AWS account. The agent can stop EC2 instances and cap Lambda concurrency.

## Tests

```bash
pytest tests -v
```

## Render

`render.yaml` deploys the Dockerized backend. Set secrets there: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `DATABASE_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`. Keep `GLOBAL_AUTOMATION_ENABLED=false` and `DRY_RUN_MODE=true` until the kill-switch has been rehearsed.
