# Atomic Verify-Success Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `ActionRunner.verify_pending()` confirms an action reached its target state, all of its follow-up writes commit together or not at all.

**Architecture:** Today the success branch in `backend/app/services/action_runner.py:331-348` makes four separate PostgREST writes:

1. mark the action `completed`;
2. update the resource state;
3. write the `action_verified` audit row;
4. `_after_success`: resolve the anomaly, or mark the original action `rolled_back`.

A crash or failed write in between leaves a half-finished result. Because the verify job only selects `executing` rows, the remaining steps never run.

This plan moves all four writes into one plpgsql function, `complete_verified_action`, called once via `self.db.rpc(...)`. PostgREST runs each RPC in a single transaction. Python still decides what to write (target state, forward vs. rollback); SQL only applies it.

**Why not asyncpg:** `verify_pending` is synchronous supabase-py code run on a worker thread (`scheduler._run_sync`). The asyncpg pool is bound to the event loop, and `tests/conftest.py::fake_db` can't fake it.

**Tech Stack:** Postgres 15 plpgsql, supabase-py `Client.rpc`, pytest + moto, GitHub Actions `migrations` job (real Postgres).

**Spec:** this conversation's findings. The verify-success branch is the target; the verify-timeout branch (`action_runner.py:350-366`) is explicitly out of scope.

## Global Constraints

- Migrations are numbered contiguously (`tests/test_migrations.py::test_migrations_are_numbered_contiguously`). The new file is `db/migrations/014_complete_verified_action.sql`.
- From 011 on, every migration records itself: `INSERT INTO schema_migrations (version) VALUES (14) ON CONFLICT (version) DO NOTHING;`.
- The API refuses to start unless `MAX(schema_migrations.version)` is at least the newest migration file (`backend/app/db/schema.py`). Migration 014 must be applied before deploying the code change, and startup enforces this.
- Migrations are idempotent (CI runs `scripts/apply_migrations.py` twice): use `CREATE OR REPLACE FUNCTION`.
- Grants follow the existing `DO $$ ... IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ...)` pattern. CI creates only `anon` and `authenticated`, not `service_role`.
- `audit_logs` is append-only (migration 008). The function only INSERTs into it.
- No `git commit`: each task ends with a `git add` list for the user to stage.

## Review Focus

1. **A failure partway through must leave the action `executing` and nothing else changed.** Then the next `verify_pending` cycle retries cleanly. Covered by `test_verify_success_is_all_or_nothing` (Task 2) and the trigger-forced failure in `scripts/check_complete_verified_action.py` (Task 1).
2. **Another writer moved the action out of `executing`** between the select and the RPC (stale-claim sweep, another replica). The RPC must return `false`, write nothing (no audit row, anomaly untouched), and the action must not be counted. Covered by `test_verify_success_skips_action_that_left_executing` (Task 2) and the second-call check in Task 1's script.
3. **One action's RPC raising must not block the other actions in the same sweep.** Today any exception aborts the whole loop. Covered by `test_rpc_failure_does_not_block_other_verifications` (Task 2).
4. **The function must not be callable by `anon` or `authenticated` through PostgREST.** Functions get `EXECUTE` for `PUBLIC` by default, and with the service role behind it this is a privilege-escalation path. Covered by `test_migration_014_function_is_service_role_only` (Task 1) and the `has_function_privilege` check in Task 1's script.
5. **The fake RPC drifting from the SQL function.** Tests would pass against behavior production doesn't have. Covered by running the same scenarios (forward, rollback, raced, failure) against the fake in Task 2 and against real Postgres in Task 1.

---

### Task 1: `complete_verified_action` SQL function

**Files:**
- Create: `db/migrations/014_complete_verified_action.sql`
- Create: `scripts/check_complete_verified_action.py`
- Modify: `tests/test_migrations.py` (append tests)
- Modify: `.github/workflows/ci.yml` (`migrations` job: one step after "Health's stale-resource query runs on the real schema")

**Interfaces:**
- Produces this SQL function, which PostgREST exposes at `rpc/complete_verified_action`:
  ```sql
  public.complete_verified_action(
      p_action_id      uuid,
      p_observed       jsonb,     -- observed state: 'stopped', or an int concurrency
      p_resource_state text,      -- NULL = leave resources.state alone
      p_is_rollback    boolean,   -- false: resolve anomaly; true: mark original rolled_back
      p_message        text
  ) RETURNS boolean               -- false = action was not 'executing'; nothing written
  ```

- [ ] **Step 1: Write the failing static tests** in `tests/test_migrations.py`

```python
SQL_014 = "014_complete_verified_action.sql"

def test_migration_014_defines_the_verify_rpc_and_records_itself():
    sql = _sql(SQL_014)
    assert "CREATE OR REPLACE FUNCTION public.complete_verified_action(" in sql
    assert "SET search_path = public" in sql
    assert "INSERT INTO schema_migrations (version) VALUES (14)" in sql
    assert "NOTIFY pgrst, 'reload schema'" in sql

def test_migration_014_function_is_service_role_only():
    sql = _sql(SQL_014)
    for role in ("PUBLIC", "anon", "authenticated"):
        assert re.search(rf"REVOKE ALL ON FUNCTION public\.complete_verified_action\([^)]*\) FROM {role}", sql), role
    assert re.search(r"GRANT EXECUTE ON FUNCTION public\.complete_verified_action\([^)]*\) TO service_role", sql)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_migrations.py -q`
Expected: the two new tests FAIL with `FileNotFoundError` for `014_complete_verified_action.sql`. The existing tests pass.

- [ ] **Step 3: Write `db/migrations/014_complete_verified_action.sql`**

Header comment in the style of 008/011: what the migration does, "Idempotent. Run after 001-013.", and "apply before deploying the API release that calls it". Function body, in this order, `LANGUAGE plpgsql`, `SECURITY INVOKER` (the default), `SET search_path = public`:
1. `UPDATE optimization_actions SET status = 'completed', verified_at = NOW(), post_state = jsonb_build_object('state', p_observed, 'verified', true) WHERE id = p_action_id AND status = 'executing' RETURNING id, resource_id, anomaly_id, action_type INTO ...`. `IF NOT FOUND THEN RETURN false; END IF;`
2. `IF p_resource_state IS NOT NULL AND <resource_id> IS NOT NULL`: `UPDATE resources SET state = p_resource_state WHERE id = <resource_id>`.
3. `INSERT INTO audit_logs (event_type, actor, resource_id, action_id, aws_api_call, response_status, message)` with the values `'action_verified', 'SYSTEM', <resource_id>, p_action_id, <action_type>, 'completed', p_message`.
4. `IF p_is_rollback`: `UPDATE optimization_actions SET status = 'rolled_back' WHERE rollback_action_id = p_action_id`. `ELSIF <anomaly_id> IS NOT NULL`: `UPDATE anomalies SET status = 'resolved', resolved_at = NOW() WHERE id = <anomaly_id> AND status = 'active'`.
5. `RETURN true`.

After the function:
- `REVOKE ALL ON FUNCTION public.complete_verified_action(uuid, jsonb, text, boolean, text) FROM PUBLIC;`
- a `DO $$` block that, for each of `anon`, `authenticated` and `service_role` that exists, REVOKEs from the first two and GRANTs EXECUTE to `service_role`;
- `INSERT INTO schema_migrations (version) VALUES (14) ON CONFLICT (version) DO NOTHING;`
- `NOTIFY pgrst, 'reload schema';`

The GRANT to `service_role` sits inside `IF EXISTS`, so the static regex still finds it.

- [ ] **Step 4: Run the static tests**

Run: `python -m pytest tests/test_migrations.py -q`
Expected: all PASS, including `test_migrations_are_numbered_contiguously`.

- [ ] **Step 5: Write `scripts/check_complete_verified_action.py`** (real-Postgres check, run by CI)

An asyncpg script reading `DATABASE_URL`, in the style of the existing heredoc step. It seeds its own rows: a resource `provider_id='i-ci-verify'` (ec2, us-east-1, running), an active anomaly, and actions in `executing`. Then it asserts:
- **Forward:** `SELECT complete_verified_action($1, '"stopped"'::jsonb, 'stopped', false, 'Verified target state: stopped')` returns `true`. The action is `completed` with `verified_at IS NOT NULL` and `post_state = {"state": "stopped", "verified": true}`. The resource state is `stopped`, the anomaly is `resolved`, and there is exactly 1 `action_verified` audit row for the action.
- **Second call:** the same call returns `false`, and the audit row count is still 1.
- **Rollback:** an original action is `completed` with `rollback_action_id = R`, and R is `executing`. Calling with `p_is_rollback = true` returns `true`, R is `completed` and the original is `rolled_back`.
- **Atomicity:** inside `async with conn.transaction()`:
  - create a `BEFORE INSERT` trigger on `audit_logs` that raises;
  - call the function on a fresh `executing` action inside a nested transaction (savepoint) and expect `asyncpg.RaiseError`;
  - after the savepoint rolls back, assert the action is still `executing` and its anomaly is still `active`;
  - then raise a sentinel exception to roll back the outer transaction, so the trigger disappears.
- **Privileges:** `SELECT has_function_privilege('anon', 'complete_verified_action(uuid,jsonb,text,boolean,text)', 'EXECUTE')` is `false`, and the same for `authenticated`.

The script ends with `print("complete_verified_action: OK")`.

- [ ] **Step 6: Add the CI step** to `.github/workflows/ci.yml` in the `migrations` job, after the health step:

```yaml
      - name: Verify-success RPC is atomic on the real schema
        run: python scripts/check_complete_verified_action.py
```

- [ ] **Step 7: Run it against a real Postgres** (if Docker is available locally; otherwise CI is the check)

Run:
```bash
docker run -d --name cs-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:15
```
Then, with `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres`:
1. create the `anon` and `authenticated` roles, as in the CI step;
2. `python scripts/apply_migrations.py`, twice;
3. `python scripts/check_complete_verified_action.py`.

Expected: the second apply is a no-op, and the check prints `complete_verified_action: OK`.

- [ ] **Step 8: Stage**

```bash
git add db/migrations/014_complete_verified_action.sql scripts/check_complete_verified_action.py tests/test_migrations.py .github/workflows/ci.yml
```

---

### Task 2: ActionRunner calls the RPC; the fake implements it

**Files:**
- Modify: `tests/fakes.py` (`FakeSupabase`: add `rpc`, `fail_rpcs`)
- Modify: `backend/app/services/action_runner.py:331-348` (success branch), `:369-381` (delete `_after_success`; `_resolve_anomaly` stays, since `:267` still uses it)
- Test: `tests/test_action_runner.py`

**Interfaces:**
- Consumes: the `complete_verified_action` parameters from Task 1, exactly as named (`p_action_id`, `p_observed`, `p_resource_state`, `p_is_rollback`, `p_message`).
- Produces:
  - `FakeSupabase.rpc(fn: str, params: dict) -> object` whose `.execute()` returns `_Result(data=<bool>)`. It records `("rpc", fn)` in `self.calls`.
  - `FakeSupabase.fail_rpcs: dict[str, Callable[[dict], bool]]`, empty by default. When the predicate returns true for `params`, `execute()` raises `FakeAPIError` before writing anything.
  - `ActionRunner._complete_verified(self, action: dict, observed, resource: dict) -> bool`

- [ ] **Step 1: Write the failing tests** in `tests/test_action_runner.py` (next to `test_stop_verify_rollback_cycle`, reusing `_instance`, `_seed`, `_live`, `_action`, `_state`)

```python
def _verified_rows(fake_db, aid="a1"):
    return [l for l in fake_db.rows("audit_logs") if l["event_type"] == "action_verified" and l.get("action_id") == aid]

def test_verify_success_is_all_or_nothing(aws, fake_db):
    iid = _instance(); _seed(fake_db, iid); _live()
    runner = ActionRunner()
    assert runner.execute_action("a1") is True
    fake_db.fail_rpcs["complete_verified_action"] = lambda p: True
    assert runner.verify_pending() == 0
    assert _action(fake_db)["status"] == "executing"
    assert fake_db.rows("anomalies")[0]["status"] == "active"
    assert fake_db.rows("resources")[0]["state"] == "running"
    assert _verified_rows(fake_db) == []
    fake_db.fail_rpcs.clear()
    assert runner.verify_pending() == 1          # retried next cycle
    assert _action(fake_db)["status"] == "completed"
    assert fake_db.rows("anomalies")[0]["status"] == "resolved"
    assert len(_verified_rows(fake_db)) == 1

def test_verify_success_skips_action_that_left_executing(aws, fake_db, monkeypatch):
    iid = _instance(); _seed(fake_db, iid); _live()
    runner = ActionRunner()
    runner.execute_action("a1")
    def raced(pid):                               # stale-claim sweep wins the race
        _action(fake_db)["status"] = "failed"
        return "stopped"
    monkeypatch.setattr(runner.cloud, "get_instance_state", raced)
    assert runner.verify_pending() == 0
    assert _action(fake_db)["status"] == "failed"
    assert fake_db.rows("anomalies")[0]["status"] == "active"
    assert _verified_rows(fake_db) == []

def test_rpc_failure_does_not_block_other_verifications(aws, fake_db):
    # Two resources, each with its own executing stop_ec2 action.
    # Seed r1/a1 with _seed, then append r2/an2/a2 rows in the same shape.
    # Fail the RPC only for a1:
    fake_db.fail_rpcs["complete_verified_action"] = lambda p: p["p_action_id"] == "a1"
    # Expected: verify_pending() == 1; a1 stays "executing"; a2 is "completed".
```

The third test's body follows the comments: execute both actions with `runner.execute_action`, then assert as stated.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_action_runner.py -q -k "all_or_nothing or left_executing or block_other"`
Expected: FAIL with `AttributeError: 'FakeSupabase' object has no attribute 'fail_rpcs'`.

- [ ] **Step 3: Implement `FakeSupabase.rpc` and `fail_rpcs`** in `tests/fakes.py`

`rpc` returns a small object whose `execute()`:
1. appends `("rpc", fn)` to `calls`;
2. raises `FakeAPIError` if `fail_rpcs.get(fn)` is set and returns true for `params`;
3. dispatches `complete_verified_action` to a module-level `_complete_verified_action(db, params) -> bool` that applies Task 1's five SQL steps to the in-memory tables, in the same order and under the same guards (`status == "executing"`, anomaly `status == "active"`). The audit row's `created_at` is a `datetime.now(timezone.utc).isoformat()` string.

Any other `fn` raises `NotImplementedError(fn)`. Update the module docstring's subset list to mention RPCs.

- [ ] **Step 4: Implement `ActionRunner._complete_verified`** and rewrite the success branch

`_complete_verified` calls `self.db.rpc("complete_verified_action", {...}).execute()` and returns `bool(res.data)`. The arguments are:
- `p_action_id = action["id"]`
- `p_observed = observed`
- `p_resource_state = observed if action["action_type"] in VERIFY_TARGETS and resource.get("id") else None`, using the `resource` argument (the loop's `action.get("resources") or {}`)
- `p_is_rollback = action["action_type"] not in REVERSIBLE`; this matches today's `_after_success`, since `"apply_tags"` is already a `REVERSIBLE` key
- `p_message = f"Verified target state: {observed}"`

In `verify_pending`, the `if ok:` branch becomes:

```python
            if ok:
                try:
                    done = self._complete_verified(action, observed, resource)
                except Exception:
                    logger.exception("Could not record verification of %s; retrying next cycle", action["id"])
                    continue
                if done:
                    verified += 1
```

Delete `_after_success`. Keep `_resolve_anomaly`.

- [ ] **Step 5: Run the new tests**

Run: `python -m pytest tests/test_action_runner.py -q -k "all_or_nothing or left_executing or block_other"`
Expected: 3 passed.

- [ ] **Step 6: Run the full suite** (existing verify tests now go through the fake RPC)

Run: `python -m pytest -q`
Expected: all pass. In particular `test_stop_verify_rollback_cycle` (forward + rollback), `test_limit_lambda_sets_positive_cap_and_restores` (int observed, no resource-state write), `test_rollback_verify_timeout_releases_link` (timeout path unchanged) and `tests/test_integration.py::test_full_loop_idle_ec2`.

- [ ] **Step 7: Stage**

```bash
git add tests/fakes.py backend/app/services/action_runner.py tests/test_action_runner.py
```
