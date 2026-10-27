---
last_mapped_commit: 7c29211f4ff4cdfd1dad8c9001b1fadd2b22c34f
last_mapped_at: 2026-10-23
---
# Codebase Concerns

**Analysis Date:** 2026-10-23

## Tech Debt

**Action runner is a single large class:**
- Issue: `ActionRunner` holds discovery-independent claim, execute, verify, rollback, expiry, and cap logic in one ~700-line class with many responsibilities and multiple `except Exception` branches.
- Files: `backend/app/services/action_runner.py`
- Impact: Changes to one lifecycle step (e.g. rollback) easily break another; the working tree already carries uncommitted edits to this file, so review risk is high.
- Fix approach: Split into claim/execute, verify, and rollback modules sharing a small state-transition helper; keep the status transitions in one table-driven place.

**Rate limiting is per process and in memory:**
- Issue: `SlidingWindowLimiter` state lives in `_limiters` and `_hits` dicts in the process. The module docstring acknowledges N replicas allow N x the limit.
- Files: `backend/app/rate_limit.py`
- Impact: Limits are not a global cap. Also `_evict` calls `self._hits.clear()` when still full after evicting idle keys, which resets every client's window at once under a key-flood.
- Fix approach: Use a shared store (Redis or Postgres) if a global cap is needed; at minimum replace the full clear with LRU eviction.

**Module-level singletons and mutable global caches:**
- Issue: `_jwks_cache` (`backend/app/auth.py`), `_last_sent` (`backend/app/services/alerts.py`), `_services` (`backend/app/scheduler.py`), `_counters`/`_summaries` (`backend/app/metrics.py`), `_regional` (`backend/app/services/action_runner.py`), and `_conn` / `_pool` / `_client` globals (`backend/app/db/leader.py`, `backend/app/db/asyncpg_pool.py`, `backend/app/db/supabase_client.py`) are process-wide mutable state.
- Files: as listed above
- Impact: Tests must reset state manually; cross-test leakage is possible; multi-worker deployments each hold their own copies (alert throttling and metrics are per process).
- Fix approach: Wrap in explicit reset hooks used by the test fixtures, and document per-process semantics in each module.

**Migrations applied out of band:**
- Issue: `db/migrations/014_complete_verified_action.sql` and `015_fail_timed_out_action.sql` are untracked and must be applied before the API release that calls `db.rpc("complete_verified_action", ...)` (`backend/app/services/action_runner.py` around line 370) and `fail_timed_out_action` (around line 385). The header of 014 states the verify job fails every verification if they are missing.
- Files: `db/migrations/014_complete_verified_action.sql`, `db/migrations/015_fail_timed_out_action.sql`, `scripts/apply_migrations.py`, `scripts/check_complete_verified_action.py`
- Impact: Deploying code ahead of the migration silently breaks verification; there is no startup check that the function exists.
- Fix approach: Add a startup check (the `schema_version` mechanism from migration 011 can gate this) and make the apply step part of the deploy runbook.

**Duplicate/partial test scaffolding:**
- Issue: `tests/fakes.py` and per-test fakes diverge; `tests/test_migrations.py` and `tests/test_providers.py` are modified but not yet committed.
- Files: `tests/fakes.py`, `tests/test_migrations.py`, `tests/test_providers.py`, `tests/test_action_runner.py`
- Impact: Fake DB behaviour may not match PostgREST/RPC semantics, so tests can pass while the real call path fails.
- Fix approach: Make `fakes.py` the single fake and add a contract test per RPC function.

## Known Bugs and Risky Behaviour

**Verification depends on a DB function that may be absent:**
- Symptoms: Executed actions stay `executing` or fail verification when the RPC is missing.
- Files: `backend/app/services/action_runner.py`, `db/migrations/014_complete_verified_action.sql`
- Trigger: API deployed before migrations 014/015 are applied.
- Workaround: Apply migrations 014 and 015 before deploy; the stale-claim sweep (`fail_stale_claims`) eventually recovers stuck claims.

**Broad exception swallowing:**
- Symptoms: Failures are logged at warning/error level but the caller receives only `False` or `0`, hiding the cause.
- Files: `backend/app/services/action_runner.py` (lines ~242, 335, 349, 453, 513, 555, 571, 586), `backend/app/services/anomaly_detector.py` (lines ~67, 82), `backend/app/api/resources.py` (~154), `backend/app/api/system.py` (~58), `backend/app/db/leader.py` (~49, 61-62, where a bare `pass` follows a failed close), `backend/app/services/runtime_config.py` (~269)
- Trigger: Any transient Supabase/PostgREST or cloud API error.
- Workaround: Read the logs; the secret-scrub filter in `backend/app/logging_config.py` keeps them safe to read.

**Mutating action endpoints and dry-run semantics:**
- Symptoms: Dry-run and real executions share one code path; a claim with `executed_at` still NULL is counted conservatively against caps, which can block legitimate actions for a while.
- Files: `backend/app/services/safety_layer.py` (`_real_executions_since`), `backend/app/services/action_runner.py`
- Trigger: Concurrent executions or a crash between claim and submit.
- Workaround: Wait for the stale-claim sweep.

## Security Considerations

**Secrets on disk in the project root:**
- Risk: A `.env` file with live configuration exists at the repo root.
- Files: `.env`, `.env.example`
- Current mitigation: `.env` is gitignored (`.gitignore` line 125) and not tracked in git; gitleaks runs in CI (`.github/workflows/ci.yml`); `backend/app/logging_config.py` scrubs DSN passwords and `secret|password|api_key|token` values from logs.
- Recommendations: Rotate any credential that has ever been pasted into chat, logs, or a shared drive; keep `SUPABASE_SERVICE_ROLE_KEY` and `AWS_SECRET_ACCESS_KEY` out of `.env` where a secret manager is available.

**Service-role database access:**
- Risk: The backend uses `SUPABASE_SERVICE_ROLE_KEY` and a direct `DATABASE_URL`, both of which bypass RLS.
- Files: `backend/app/db/supabase_client.py`, `backend/app/db/asyncpg_pool.py`, `backend/app/db/leader.py`, `db/migrations/003_rls_policies.sql`, `db/migrations/006_security_and_seed.sql`, `db/migrations/009_grants_and_constraints.sql`
- Current mitigation: Authorization is enforced in the API layer (`backend/app/auth.py`, `OPERATOR_USER_IDS`, `VIEWER_USER_IDS`, `REQUIRE_VIEWER_ROLE`); column-level GRANTs restrict authenticated writes; `audit_logs` is append-only via triggers (migration 008).
- Recommendations: Keep `REQUIRE_VIEWER_ROLE=true` in production; add tests that the anon/authenticated roles cannot call the `service_role`-only functions from 014/015.

**JWT verification source is optional:**
- Risk: Startup only warns when no verification source is configured.
- Files: `backend/app/main.py` (lines ~37-38), `backend/app/auth.py` (HS256 path line ~129, JWKS fetch lines ~82-98)
- Current mitigation: Startup problem list in `main.py`; JWKS cache has a TTL.
- Recommendations: Make a missing verification source fatal when `ENVIRONMENT=production`.

**CORS and docs exposure:**
- Risk: Misconfigured `CORS_ORIGINS` or `EXPOSE_API_DOCS=true` in production exposes the full API map.
- Files: `backend/app/main.py` (lines ~50-51, 140-151), `backend/app/config.py`
- Current mitigation: `*` in `CORS_ORIGINS` is ignored with a warning; docs default to off; `/metrics` requires `METRICS_TOKEN`.
- Recommendations: Fail startup on `*` rather than warning.

**Webhook and alert egress:**
- Risk: `ALERT_WEBHOOK_URL` is posted to from the backend with no allow-list.
- Files: `backend/app/services/alerts.py`
- Current mitigation: Documented as https-only; throttled per alert key via `ALERT_MIN_INTERVAL_SECONDS`.
- Recommendations: Validate the scheme and host at config load.

**Dependency audit:**
- Risk: Unpinned or drifting transitive dependencies.
- Files: `backend/requirements.txt`, `.github/workflows/ci.yml` (pip-audit `--strict`)
- Current mitigation: pip-audit runs in CI, but only against `backend/requirements.txt`, not the `requirements*.txt` cache path it lists.
- Recommendations: Audit all requirement files and the `ml/` dependencies.

## Performance Bottlenecks

**Per-action database round-trips:**
- Problem: `execute_action` and the verify loop issue several PostgREST queries per action (select with embedded resource, claim, audit insert, cap queries, finish).
- Files: `backend/app/services/action_runner.py`, `backend/app/services/safety_layer.py`
- Cause: One query per guard check; `execute_pending_auto` processes up to 50 actions sequentially.
- Improvement path: Batch cap queries per resource, and move multi-step transitions into SQL functions as migration 014 already does.

**Cloud API fan-out in discovery and metrics:**
- Problem: Discovery iterates `AWS_REGIONS` and resources sequentially.
- Files: `backend/app/services/discovery.py`, `backend/app/adapters/aws.py`, `backend/app/adapters/gcp.py` (metric series grouping around lines 147-164)
- Cause: Synchronous boto3/GCP client calls inside async jobs.
- Improvement path: Run per-region work in a thread pool with bounded concurrency.

**ML pipeline on the request path:**
- Problem: Anomaly scoring and feature building run in the same process as the API.
- Files: `backend/app/services/anomaly_detector.py`, `ml/features.py`, `ml/inference.py`
- Cause: pandas `iloc` and `to_dict` per resource.
- Improvement path: Run pipeline jobs only in the scheduler process (`SCHEDULER_ENABLED`) and keep API replicas light.

## Fragile Areas

**Action state machine:**
- Files: `backend/app/services/action_runner.py` (`_claim`, `execute_action`, `verify_pending`, `rollback_action`), `db/migrations/014_complete_verified_action.sql`, `db/migrations/015_fail_timed_out_action.sql`
- Why fragile: Status values are strings across Python, SQL, and the frontend (`ActionStatus` in `backend/app/api/actions.py`). Adding a status requires edits in several places.
- Safe modification: Change the status enum in one place and update both SQL functions and the `_claim` guard together; run `tests/test_action_runner.py` and `tests/test_migrations.py`.
- Test coverage: Migrations are exercised by `tests/test_migrations.py`, but the RPC contract is only partially faked in `tests/fakes.py`.

**Policy and safety caps:**
- Files: `backend/app/services/policy_engine.py`, `backend/app/services/safety_layer.py`, `db/migrations/004_seed_policies.sql`, `db/migrations/010_recommend_review_policy.sql`, `db/migrations/013_rds_review_policies.sql`
- Why fragile: Policy behaviour is spread across seed SQL migrations and Python; later migrations rewrite earlier seeds.
- Safe modification: Add new policies as a new migration, never by editing a seeded row in an earlier one.

**Frontend routing and auth:**
- Files: `frontend/src/auth/AuthProvider.tsx`, `frontend/src/lib/supabase.ts`, `frontend/src/App.tsx`
- Why fragile: Password-recovery flow depends on the `PASSWORD_RECOVERY` event and PKCE redirect to `/reset-password`.
- Safe modification: Test the reset flow manually after any Supabase client upgrade.

## Scaling Limits

**In-process state:**
- Current capacity: One scheduler is elected by `backend/app/db/leader.py`; rate limits and alert throttles are per process.
- Limit: Throughput and rate limits scale with replicas rather than being global.
- Scaling path: Shared limiter and alert throttle store (Postgres table or Redis).

**Action batch size:**
- Current capacity: `execute_pending_auto` handles 50 actions per run (`.limit(50)`).
- Limit: Backlog grows if discovery produces more proposals than that per schedule tick.
- Scaling path: Make the batch size a `runtime_config` setting and drain in a loop until empty or a time budget is hit.

## Dependencies at Risk

**Supabase Python client and asyncpg both in use:**
- Risk: Two database access paths (PostgREST via `supabase_client.py`, direct asyncpg via `asyncpg_pool.py`) with different transaction semantics.
- Impact: Multi-step writes done through PostgREST are not atomic, which is the reason migration 014 exists.
- Migration plan: Route all multi-step state changes through SQL functions and keep PostgREST for single-row reads and writes.

**Cloud SDKs:**
- Risk: boto3 and google-cloud libraries are pinned loosely in `backend/requirements.txt`.
- Impact: Adapter behaviour changes silently between deployments.
- Migration plan: Pin exact versions and run `tests/test_aws_adapter.py` and `tests/test_providers.py` on upgrade.

## Missing Critical Features

**Global rate limiting:**
- Problem: No shared limiter across replicas.
- Blocks: Safe horizontal scaling of the API under abuse.

**Automated migration verification at startup:**
- Problem: The app does not check that required SQL functions and `schema_version` are present before serving traffic.
- Blocks: Safe rollout of migrations 014 and 015 without manual sequencing.

## Test Coverage Gaps

**Real RPC and PostgREST behaviour:**
- What's not tested: Calls to `complete_verified_action` and `fail_timed_out_action` against a real database; concurrent claims from two replicas.
- Files: `backend/app/services/action_runner.py`, `tests/fakes.py`, `tests/test_action_runner.py`, `scripts/check_complete_verified_action.py`
- Risk: Verification or claim logic can break in production while unit tests pass.
- Priority: High

**Rate limiter eviction and multi-key behaviour:**
- What's not tested: The full-clear path in `SlidingWindowLimiter._evict` under a key flood.
- Files: `backend/app/rate_limit.py`
- Risk: A flood of spoofed client IPs silently removes all limits.
- Priority: Medium

**Frontend:**
- What's not tested: No frontend test files were found under `frontend/`.
- Files: `frontend/src/`
- Risk: Auth and action-approval UI regressions go unnoticed.
- Priority: Medium

**Uncommitted working tree:**
- What's not tested: Changes in `backend/app/services/action_runner.py`, `backend/app/adapters/gcp.py`, `tests/*.py`, and CI (`.github/workflows/ci.yml`) are not yet committed, so CI results reflect the last commit only.
- Files: as listed
- Risk: Verified locally but not exercised in CI.
- Priority: High

---

*Concerns audit: 2026-10-23*
