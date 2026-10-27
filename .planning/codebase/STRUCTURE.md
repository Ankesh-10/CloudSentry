---
last_mapped_commit: 7c29211f4ff4cdfd1dad8c9001b1fadd2b22c34f
last_mapped_at: 2026-10-23
---
# Codebase Structure

**Analysis Date:** 2026-10-23

## Directory Layout

```
CloudSentry/
├── backend/                 # FastAPI service (API + scheduler host)
│   ├── app/
│   │   ├── adapters/        # CloudAdapter ABC, AWS and GCP implementations, factory
│   │   ├── api/             # One router module per REST resource
│   │   ├── db/              # asyncpg pool, Supabase client, leader lock, schema check
│   │   ├── schemas/         # Pydantic models
│   │   └── services/        # Domain logic (discovery, telemetry, actions, policy, ...)
│   ├── Dockerfile
│   └── requirements*.txt
├── frontend/                # React + Vite SPA
│   ├── src/
│   │   ├── auth/            # AuthProvider
│   │   ├── components/      # Layout, charts, UI primitives, command palette
│   │   ├── hooks/           # Query hooks, URL state
│   │   ├── lib/             # API client, Supabase client, env, formatting, types
│   │   ├── pages/           # Route-level screens (lazy loaded)
│   │   ├── test/            # Vitest + Testing Library tests
│   │   └── theme/           # ThemeProvider
│   ├── dist/                # Built assets (generated)
│   └── package.json
├── ml/                      # Anomaly model code and synthetic data generator
│   └── synthetic/scenarios/ # JSON scenarios for demos and tests
├── db/migrations/           # Ordered SQL migrations 001 to 015
├── tests/                   # Pytest suite (flat, one file per concern)
├── scripts/                 # Operational and demo scripts
├── terraform/               # AWS infrastructure (main, iam, variables, outputs)
├── cloud_permissions/       # IAM policy (AWS JSON, GCP custom role YAML)
├── data/pricing/            # Static price lists used by cost estimator
├── docs/superpowers/        # Design and planning notes
├── .claude/                 # Claude Code settings and hooks
├── .github/workflows/       # CI (ci.yml)
├── .planning/               # GSD planning state and generated codebase docs
├── docker-compose.yml, render.yaml, pytest.ini, ruff.toml, .gitleaks.toml
└── README.md, CHANGES.md
```

## Directory Purposes

**`backend/app/api/`:**
- Purpose: HTTP surface, one module per resource.
- Contains: `actions.py`, `anomalies.py`, `audit.py`, `dashboard.py`, `metrics.py`, `policies.py`, `resources.py`, `system.py`, plus `__init__.py`.
- Key files: `backend/app/api/actions.py` (approve, execute, rollback), `backend/app/api/resources.py`.
- Mounted in `backend/app/main.py` under `/api/v1/{resources,metrics,anomalies,actions,dashboard,system,audit-logs,policies}`.

**`backend/app/services/`:**
- Purpose: All domain behavior. Routers and scheduler both import from here.
- Contains: Discovery, telemetry, anomaly detection, policy engine, action runner, safety layer, two-person approval, audit logging, cost estimation, ML retraining, retention, alerts, health, job status, runtime config.
- Key files: `backend/app/services/action_runner.py`, `backend/app/services/safety_layer.py`, `backend/app/services/policy_engine.py`, `backend/app/services/telemetry.py`.

**`backend/app/adapters/`:**
- Purpose: Cloud provider boundary.
- Key files: `backend/app/adapters/base.py` (interface), `backend/app/adapters/factory.py` (selection), `backend/app/adapters/aws.py`, `backend/app/adapters/gcp.py`.

**`backend/app/db/`:**
- Purpose: Database access and cluster-level coordination.
- Key files: `backend/app/db/asyncpg_pool.py` (pool lifecycle, `is_transaction_pooler`), `backend/app/db/supabase_client.py`, `backend/app/db/leader.py`, `backend/app/db/schema.py`, `backend/app/db/pagination.py`.

**`backend/app/` top-level modules:**
- `main.py`: FastAPI app, lifespan, middleware, routers, `/metrics`, health routes.
- `scheduler.py`: APScheduler jobs and leader-only gating.
- `auth.py`: JWT verification and role dependencies.
- `config.py`: `Settings` (pydantic-settings) with env-driven values such as `CLOUD_PROVIDER`, `SCHEDULER_ENABLED`, `JOB_*_MINUTES`.
- `metrics.py`, `rate_limit.py`, `request_context.py`, `logging_config.py`: cross-cutting infrastructure.

**`frontend/src/pages/`:**
- Purpose: One file per route. Lazy-loaded from `frontend/src/App.tsx`.
- Contains: `Dashboard.tsx`, `Resources.tsx`, `ResourceDetail.tsx`, `Anomalies.tsx`, `Actions.tsx`, `AuditLogs.tsx`, `System.tsx`, `Login.tsx`, `ResetPassword.tsx`.

**`frontend/src/components/`:**
- Purpose: Shared UI. `ui.tsx` holds primitives (`Button`, `Card`, `Badge`, `Empty`, `Loading`, `ErrorBox`, `Json`); `charts.tsx` holds `Sparkline`, `TrendArea`, `StackedBar`, `BarList`; `Layout.tsx` is the shell; `nav.ts` defines navigation items; `CommandPalette.tsx`, `Dialog.tsx`, `Toast.tsx`, `insights.tsx`.

**`frontend/src/lib/`:**
- Purpose: Non-React utilities. `api.ts` (fetch wrapper with token refresh), `supabase.ts` (auth client and session helpers), `env.ts` (`missingConfig`), `role.ts`, `format.ts`, `types.ts`, `demo.ts`.

**`frontend/src/hooks/`:**
- `queries.ts` (data hooks), `useUrlState.ts` (filter state in URL).

**`ml/`:**
- Purpose: Feature engineering, scoring, baseline heuristics, training, evaluation.
- Key files: `ml/features.py`, `ml/inference.py`, `ml/trainer.py`, `ml/baseline.py`, `ml/evaluation.py`, `ml/synthetic/generator.py`.

**`db/migrations/`:**
- Purpose: Versioned SQL schema. Applied by `scripts/apply_migrations.py`; version checked at boot by `backend/app/db/schema.py`.
- Naming: `NNN_snake_case_description.sql` with a three-digit, zero-padded sequence.
- Current range: `001_initial_schema.sql` through `015_fail_timed_out_action.sql`.

**`tests/`:**
- Purpose: Backend pytest suite, flat layout.
- Naming: `test_<module_or_concern>.py`. Shared fixtures in `tests/conftest.py`; in-memory fakes in `tests/fakes.py`.
- Examples: `tests/test_action_runner.py`, `tests/test_auth.py`, `tests/test_safety_layer.py`, `tests/test_migrations.py`, `tests/test_api_surface.py`.

**`scripts/`:**
- Purpose: Operations and demos (migration runner, anomaly injection, stress runs, demo provisioning, Supabase connectivity check).

**`terraform/`, `cloud_permissions/`:**
- Purpose: Infrastructure as code and least-privilege IAM for the monitored cloud accounts. `tests/test_iam_policy.py` checks the policy files.

## Key File Locations

**Entry Points:**
- `backend/app/main.py`: API application object `app`.
- `frontend/src/main.tsx`: SPA bootstrap.
- `frontend/src/App.tsx`: route table.
- `scripts/apply_migrations.py`: migration CLI.

**Configuration:**
- `backend/app/config.py`: backend settings.
- `.env.example`, `frontend/.env.example`, `frontend/.env.demo`: templates for environment values. Do not read `.env` or `frontend/.env.local`.
- `ruff.toml`, `pytest.ini`, `frontend/vite.config.ts`, `frontend/tsconfig.json`: tooling.
- `render.yaml`, `docker-compose.yml`, `backend/Dockerfile`, `.dockerignore`: deployment.
- `.claude/settings.json`, `.claude/hooks/`: agent hooks (`protect_files.py`, `post_edit_check.py`).

**Core Logic:**
- `backend/app/services/action_runner.py`: action state machine.
- `backend/app/services/policy_engine.py`: policy evaluation.
- `backend/app/scheduler.py`: job graph.
- `ml/inference.py`: runtime scoring.

**Testing:**
- `tests/`: backend tests.
- `frontend/src/test/`: `api.test.ts`, `format.test.ts`, `supabase.test.ts`, `ui.test.tsx`, `setup.ts`.

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (`action_runner.py`, `policy_engine.py`).
- Python tests: `test_<subject>.py` in `tests/`.
- React components: `PascalCase.tsx` (`Dashboard.tsx`, `ConfirmButton` lives in `ui.tsx`).
- Non-component TS modules: `camelCase.ts` or lowercase (`api.ts`, `queries.ts`, `useUrlState.ts`).
- SQL migrations: `NNN_description.sql`.

**Directories:**
- Backend packages: lowercase single words (`adapters`, `api`, `services`, `db`, `schemas`).
- Frontend: lowercase plural for groups (`pages`, `components`, `hooks`, `lib`).

**Code identifiers (observed):**
- Classes: `PascalCase` with role suffix (`AWSAdapter`, `ActionRunner`, `DiscoveryService`, `SafetyLayer`, `CostEstimationService`).
- Job IDs: `snake_case` ending in `_job` (`discovery_job`, `action_execute_job`).
- Routers: module-level `router` object, prefix set in `main.py`.

## Where to Add New Code

**New REST resource:**
- Router: `backend/app/api/<resource>.py` exposing `router = APIRouter()`.
- Register: append `(module.router, "/api/v1/<resource>", "<tag>")` to `ROUTERS` in `backend/app/main.py`. Viewer dependency is applied automatically; add `Depends(require_operator)` on mutating routes.
- Schemas: `backend/app/schemas/models.py`.
- Tests: `tests/test_<resource>.py` (follow `tests/test_policies_api.py`). Update `tests/test_api_surface.py` if it enumerates routes.

**New domain behavior:**
- Implementation: `backend/app/services/<name>.py` as a class with `run()` or a clear method entry point.
- Wire into scheduling: add a `job_*` function and `add(...)` call in `setup_scheduler()` in `backend/app/scheduler.py`; add it to `LEADER_ONLY_JOBS` if it mutates shared state.
- Lazy construction: register in the `factories` dict inside `_svc()`.
- Tests: `tests/test_<name>.py`, using `tests/fakes.py` for Supabase or asyncpg doubles.

**New cloud provider:**
- Implementation: `backend/app/adapters/<provider>.py` subclassing `CloudAdapter` from `backend/app/adapters/base.py`.
- Register: add a branch in `get_cloud_adapter` in `backend/app/adapters/factory.py`.
- Permissions: add policy under `cloud_permissions/` and extend `tests/test_iam_policy.py`.

**New database change:**
- Migration: next free number in `db/migrations/` (currently `016_...`). Include constraints and RLS grants where relevant.
- Verify with `tests/test_migrations.py`.

**New frontend page:**
- Page: `frontend/src/pages/<Name>.tsx`, default export.
- Route: add `lazy(() => import(...))` and a `<Route>` in `frontend/src/App.tsx`; add nav entry in `frontend/src/components/nav.ts`.
- Data: add a hook in `frontend/src/hooks/queries.ts`, and a typed client call in `frontend/src/lib/api.ts`; types in `frontend/src/lib/types.ts`.
- Tests: `frontend/src/test/<name>.test.tsx`.

**New shared UI primitive:**
- Location: `frontend/src/components/ui.tsx` (or `charts.tsx` for data visualization).

**Utilities:**
- Backend shared helpers: `backend/app/services/` for domain logic, `backend/app/db/` for data access.
- Frontend shared helpers: `frontend/src/lib/`.

## Special Directories

**`frontend/dist/`:**
- Purpose: Vite build output.
- Generated: Yes
- Committed: Not intended; treat as build artifact.

**`ml/synthetic/scenarios/`:**
- Purpose: Scenario definitions for synthetic data.
- Generated: No (hand-authored JSON)
- Committed: Yes

**`.planning/`:**
- Purpose: GSD project state. `.planning/codebase/` holds these maps; `.planning/graphs/` holds the graphify knowledge graph (`graph.json`, `GRAPH_REPORT.md`, `graph.html`).
- Generated: Partly (graphs and codebase docs are generated)
- Committed: Config and graph JSON are tracked; `graph.html` and `.last-build-snapshot.json` are untracked.

**`.superpowers/sdd/`:**
- Purpose: Per-plan task briefs, progress notes, and test logs from subagent-driven development.
- Generated: Yes
- Committed: Partly (`.gitignore` inside the directory governs it).

**`.claude/worktrees/`:**
- Purpose: Git worktrees created by Claude Code sessions (a full copy of the repo under `nifty-gould-7cf5da`).
- Generated: Yes
- Committed: No. Exclude from searches to avoid duplicate matches.

**`.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.coverage`, `__pycache__/`:**
- Purpose: Local Python environment and tool caches.
- Generated: Yes
- Committed: No (ignored by `.gitignore`).

**`docs/`:**
- Purpose: Currently holds `docs/superpowers/` design material. Untracked in git at time of analysis.

---

*Structure analysis: 2026-10-23*
