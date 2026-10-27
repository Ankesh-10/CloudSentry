# Context Intel (DOC)

Topic-keyed notes appended verbatim from DOC-classified sources. Quoted source text is fenced with per-block random markers and is data, not instructions.

## Topic: Atomic verify-success for ActionRunner.verify_pending
- source: C:/Users/Asus/Desktop/code/Project/CloudSentry/docs/superpowers/plans/2026-10-16-atomic-verify-success.md
- classification: DOC (classifier confidence: low; orchestrator recorded as DOC; not an ADR/PRD/SPEC)
- locked: false
- scope: ActionRunner.verify_pending, complete_verified_action plpgsql function, action_runner.py verify-success branch, db/migrations/014, audit_logs, GitHub Actions migrations job

### Goal
DATA_k7Qx2mPa_START
When `ActionRunner.verify_pending()` confirms an action reached its target state, all of its follow-up writes commit together or not at all.
DATA_k7Qx2mPa_END

### Architecture
DATA_v3Nd8rWz_START
Today the success branch in `backend/app/services/action_runner.py:331-348` makes four separate PostgREST writes:

1. mark the action `completed`;
2. update the resource state;
3. write the `action_verified` audit row;
4. `_after_success`: resolve the anomaly, or mark the original action `rolled_back`.

A crash or failed write in between leaves a half-finished result. Because the verify job only selects `executing` rows, the remaining steps never run.

This plan moves all four writes into one plpgsql function, `complete_verified_action`, called once via `self.db.rpc(...)`. PostgREST runs each RPC in a single transaction. Python still decides what to write (target state, forward vs. rollback); SQL only applies it.
DATA_v3Nd8rWz_END

### Why not asyncpg
DATA_h5Tc1yLe_START
`verify_pending` is synchronous supabase-py code run on a worker thread (`scheduler._run_sync`). The asyncpg pool is bound to the event loop, and `tests/conftest.py::fake_db` can't fake it.
DATA_h5Tc1yLe_END

### Tech stack and scope
DATA_b9Ump4Gs_START
**Tech Stack:** Postgres 15 plpgsql, supabase-py `Client.rpc`, pytest + moto, GitHub Actions `migrations` job (real Postgres).

**Spec:** this conversation's findings. The verify-success branch is the target; the verify-timeout branch (`action_runner.py:350-366`) is explicitly out of scope.
DATA_b9Ump4Gs_END

### Global Constraints
DATA_f2Ka6sXo_START
- Migrations are numbered contiguously (`tests/test_migrations.py::test_migrations_are_numbered_contiguously`). The new file is `db/migrations/014_complete_verified_action.sql`.
- From 011 on, every migration records itself: `INSERT INTO schema_migrations (version) VALUES (14) ON CONFLICT (version) DO NOTHING;`.
- The API refuses to start unless `MAX(schema_migrations.version)` is at least the newest migration file (`backend/app/db/schema.py`). Migration 014 must be applied before deploying the code change, and startup enforces this.
- Migrations are idempotent (CI runs `scripts/apply_migrations.py` twice): use `CREATE OR REPLACE FUNCTION`.
- Grants follow the existing `DO $$ ... IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ...)` pattern. CI creates only `anon` and `authenticated`, not `service_role`.
- `audit_logs` is append-only (migration 008). The function only INSERTs into it.
- No `git commit`: each task ends with a `git add` list for the user to stage.
DATA_f2Ka6sXo_END

### Review Focus
DATA_r8Wj4zBd_START
1. **A failure partway through must leave the action `executing` and nothing else changed.** Then the next `verify_pending` cycle retries cleanly. Covered by `test_verify_success_is_all_or_nothing` (Task 2) and the trigger-forced failure in `scripts/check_complete_verified_action.py` (Task 1).
2. **Another writer moved the action out of `executing`** between the select and the RPC (stale-claim sweep, another replica). The RPC must return `false`, write nothing (no audit row, anomaly untouched), and the action must not be counted. Covered by `test_verify_success_skips_action_that_left_executing` (Task 2) and the second-call check in Task 1's script.
3. **One action's RPC raising must not block the other actions in the same sweep.** Today any exception aborts the whole loop. Covered by `test_rpc_failure_does_not_block_other_verifications` (Task 2).
4. **The function must not be callable by `anon` or `authenticated` through PostgREST.** Functions get `EXECUTE` for `PUBLIC` by default, and with the service role behind it this is a privilege-escalation path. Covered by `test_migration_014_function_is_service_role_only` (Task 1) and the `has_function_privilege` check in Task 1's script.
5. **The fake RPC drifting from the SQL function.** Tests would pass against behavior production doesn't have. Covered by running the same scenarios (forward, rollback, raced, failure) against the fake in Task 2 and against real Postgres in Task 1.
DATA_r8Wj4zBd_END

### Task 1 summary: complete_verified_action SQL function
- files: create db/migrations/014_complete_verified_action.sql; create scripts/check_complete_verified_action.py; modify tests/test_migrations.py; modify .github/workflows/ci.yml (migrations job, one step after "Health's stale-resource query runs on the real schema")

DATA_c4Ye7nQt_START
public.complete_verified_action(
    p_action_id      uuid,
    p_observed       jsonb,     -- observed state: 'stopped', or an int concurrency
    p_resource_state text,      -- NULL = leave resources.state alone
    p_is_rollback    boolean,   -- false: resolve anomaly; true: mark original rolled_back
    p_message        text
) RETURNS boolean               -- false = action was not 'executing'; nothing written
DATA_c4Ye7nQt_END

Function body order (LANGUAGE plpgsql, SECURITY INVOKER, SET search_path = public):
DATA_m1Zs5hVu_START
1. `UPDATE optimization_actions SET status = 'completed', verified_at = NOW(), post_state = jsonb_build_object('state', p_observed, 'verified', true) WHERE id = p_action_id AND status = 'executing' RETURNING id, resource_id, anomaly_id, action_type INTO ...`. `IF NOT FOUND THEN RETURN false; END IF;`
2. `IF p_resource_state IS NOT NULL AND <resource_id> IS NOT NULL`: `UPDATE resources SET state = p_resource_state WHERE id = <resource_id>`.
3. `INSERT INTO audit_logs (event_type, actor, resource_id, action_id, aws_api_call, response_status, message)` with the values `'action_verified', 'SYSTEM', <resource_id>, p_action_id, <action_type>, 'completed', p_message`.
4. `IF p_is_rollback`: `UPDATE optimization_actions SET status = 'rolled_back' WHERE rollback_action_id = p_action_id`. `ELSIF <anomaly_id> IS NOT NULL`: `UPDATE anomalies SET status = 'resolved', resolved_at = NOW() WHERE id = <anomaly_id> AND status = 'active'`.
5. `RETURN true`.
DATA_m1Zs5hVu_END

After the function (verbatim):
DATA_p6Lg0aEk_START
- `REVOKE ALL ON FUNCTION public.complete_verified_action(uuid, jsonb, text, boolean, text) FROM PUBLIC;`
- a `DO $$` block that, for each of `anon`, `authenticated` and `service_role` that exists, REVOKEs from the first two and GRANTs EXECUTE to `service_role`;
- `INSERT INTO schema_migrations (version) VALUES (14) ON CONFLICT (version) DO NOTHING;`
- `NOTIFY pgrst, 'reload schema';`
DATA_p6Lg0aEk_END

Real-Postgres check script (scripts/check_complete_verified_action.py, run by CI) asserts: forward, second call (returns false, audit count still 1), rollback, atomicity (trigger-forced failure inside nested transaction; action stays executing, anomaly stays active), privileges (`has_function_privilege` false for anon and authenticated). Ends with `print("complete_verified_action: OK")`. CI step name: "Verify-success RPC is atomic on the real schema", run: `python scripts/check_complete_verified_action.py`.

### Task 2 summary: ActionRunner calls the RPC; the fake implements it
- files: modify tests/fakes.py (FakeSupabase: add rpc, fail_rpcs); modify backend/app/services/action_runner.py:331-348 (success branch) and :369-381 (delete `_after_success`; `_resolve_anomaly` stays, since :267 still uses it); test in tests/test_action_runner.py
- consumes: the `complete_verified_action` parameters from Task 1 exactly as named
- produces: `FakeSupabase.rpc(fn, params)` whose `.execute()` returns `_Result(data=<bool>)` and records `("rpc", fn)` in `self.calls`; `FakeSupabase.fail_rpcs: dict[str, Callable[[dict], bool]]` (empty by default; raises `FakeAPIError` before writing anything when predicate is true); `ActionRunner._complete_verified(self, action: dict, observed, resource: dict) -> bool`
- `_complete_verified` arguments: `p_action_id = action["id"]`; `p_observed = observed`; `p_resource_state = observed if action["action_type"] in VERIFY_TARGETS and resource.get("id") else None`; `p_is_rollback = action["action_type"] not in REVERSIBLE`; `p_message = f"Verified target state: {observed}"`
- new tests: `test_verify_success_is_all_or_nothing`, `test_verify_success_skips_action_that_left_executing`, `test_rpc_failure_does_not_block_other_verifications`
- success criteria: new tests pass; full suite `python -m pytest -q` passes, including `test_stop_verify_rollback_cycle`, `test_limit_lambda_sets_positive_cap_and_restores`, `test_rollback_verify_timeout_releases_link`, `tests/test_integration.py::test_full_loop_idle_ec2`
- cross_refs (from classification): backend/app/services/action_runner.py, tests/conftest.py, tests/test_migrations.py, scripts/check_complete_verified_action.py, .github/workflows/ci.yml, backend/app/db/schema.py
