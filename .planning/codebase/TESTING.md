---
last_mapped_commit: 7c29211f4ff4cdfd1dad8c9001b1fadd2b22c34f
last_mapped_at: 2026-10-23
---
# Testing Patterns

**Analysis Date:** 2026-10-23

## Test Framework

**Backend runner:**
- pytest with `pytest-asyncio` in auto mode and `pytest-cov` for coverage (`backend/requirements-dev.txt`).
- Config: `pytest.ini` (`pythonpath = .`, `testpaths = tests`, `asyncio_mode = auto`, `addopts = --strict-markers`, `xfail_strict = true`).
- Mocking AWS: `moto` (`mock_aws`). Mocking DB: an in-memory fake in `tests/fakes.py`.

**Frontend runner:**
- Vitest 5 with jsdom and Testing Library (`@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`), per `frontend/package.json`.
- Config: `frontend/vite.config.ts`; setup file `frontend/src/test/setup.ts`.

**Run commands:**

```bash
pytest -ra                                              # Run all backend tests (from repo root)
pytest tests/test_action_runner.py -k kill_switch       # Run one file / subset
pytest --cov=backend --cov=ml --cov-report=term-missing:skip-covered --cov-fail-under=70   # Coverage (CI form)
python -m ml.evaluation --strict                        # ML evaluation targets (CI)
cd frontend && npm test                                 # Vitest, single run (vitest run)
cd frontend && npm run test:watch                       # Vitest watch mode
cd frontend && npm run typecheck                        # tsc --noEmit
```

## Test File Organization

**Location:**
- Backend tests are all in a single top-level `tests/` directory, NOT co-located with source. The module mapping is by name: `backend/app/services/action_runner.py` -> `tests/test_action_runner.py`.
- Frontend tests are in `frontend/src/test/`, separate from components.

**Naming:**
- Python: `test_<behavior>.py` files; test functions are `test_<behavior_in_words>` (e.g. `test_kill_switch_blocks_execution_and_is_audited`, `test_db_kill_switch_overrides_stale_in_memory_flag`). Name the test after the behavior, not the method.
- TypeScript: `<area>.test.ts` / `<area>.test.tsx`; `describe` blocks name the unit under test with a contract phrase (`describe('availableOps mirrors backend guards', ...)`).

**Structure:**

```
tests/
  conftest.py              # env forcing, autouse resets, fake_db fixture
  fakes.py                 # FakeSupabase PostgREST-like in-memory client
  test_<area>.py           # one file per backend module/area (~30 files)
frontend/src/test/
  setup.ts                 # jest-dom + global setup
  api.test.ts, format.test.ts, supabase.test.ts, ui.test.tsx
```

## Test Structure

**Suite organization (Python):** Plain module-level functions, no test classes. Setup is done with small helper functions prefixed with `_` at the top of the file:

```python
def _instance(tags=None): ...          # create a moto EC2 instance
def _state(iid): ...                   # read state back from moto
def _seed(fake_db, provider_id, ...):  # insert resources/anomalies/actions into the fake DB
def _action(fake_db, aid="a1"): ...    # fetch a row for assertion
def _live(): ...                       # flip runtime flags to "automation on"
```

Source: `tests/test_action_runner.py`.

**Suite organization (TypeScript):**

```typescript
describe('availableOps mirrors backend guards', () => {
  const base: Action = { ... }          // shared fixture object, spread-overridden per case
  it('pending + requires approval: approve/reject only', () => {
    expect(availableOps(base)).toEqual(['approve', 'reject'])
  })
})
```

Source: `frontend/src/test/ui.test.tsx`.

**Patterns:**
- Setup: autouse fixtures in `tests/conftest.py` reset process-global state before and after every test (`runtime_config._cache.clear()` + `load_from_env()`, `rate_limit.reset_all()`, `health.reset_caches()`). Do not rely on state leaking between tests.
- Teardown: fixtures use `yield` for teardown (`with mock_aws(): yield`).
- Assertion: plain `assert` (pytest rewriting); `expect(...).toEqual/toBe` in Vitest.
- Test the guard/negative path explicitly. The suite routinely asserts both the blocked outcome and the audit event (`any(l["event_type"] == "action_blocked" for l in fake_db.rows("audit_logs"))`).

## Environment Isolation

`tests/conftest.py` runs `os.environ.update(_TEST_ENV)` at import time, using forced assignment (not `setdefault`) so a developer's real `.env` cannot point tests at a live Supabase project or AWS account. Key forced values:
- `DATABASE_URL=""`, `SUPABASE_URL=https://example.supabase.co`
- `AWS_*` set to `testing`
- `GLOBAL_AUTOMATION_ENABLED=false`, `DRY_RUN_MODE=true`
- `CLOUDSENTRY_TESTING=1`

When adding a setting that a test depends on, add it to `_TEST_ENV` in `tests/conftest.py`; do not rely on the developer's environment. CI needs no secrets (`.github/workflows/ci.yml`).

## Mocking

**Framework:** `moto` for AWS, `pytest` `monkeypatch` for module attributes, a custom in-memory fake for Supabase. `unittest.mock` / `MagicMock` appears in only a handful of files; prefer the existing patterns.

**AWS (preferred):**

```python
@pytest.fixture
def aws():
    with mock_aws():
        yield

def test_x(aws, fake_db):
    iid = _instance()     # real boto3 call, served by moto
```

Source: `tests/test_action_runner.py`. Use `moto` to exercise real boto3 paths rather than mocking the client.

**Database (preferred): the `fake_db` fixture.**
`tests/conftest.py` defines:

```python
@pytest.fixture
def fake_db(monkeypatch):
    from tests.fakes import FakeSupabase
    from backend.app.db import supabase_client
    from backend.app.api import actions
    db = FakeSupabase()
    monkeypatch.setattr(supabase_client, "_client", db)
    actions.get_runner.cache_clear()   # cached services hold the old client
    yield db
```

`FakeSupabase` (`tests/fakes.py`) implements the PostgREST subset the backend uses: filters filter, updates touch only matching rows, upserts honour `on_conflict`, `resources(*)` embeds resolve via FK, and partial unique indexes from migration 007 are enforced. Seed rows with `fake_db.rows("<table>").append({...})` and read them back with `fake_db.rows(...)`.

RPCs are emulated in Python and must mirror the SQL: `complete_verified_action` mirrors `db/migrations/014_complete_verified_action.sql`, `fail_timed_out_action` mirrors `db/migrations/015_fail_timed_out_action.sql`. When the SQL changes, update the fake and `scripts/check_complete_verified_action.py` together.

**Migrations:** `tests/test_migrations.py` checks the migration files; `scripts/check_complete_verified_action.py` cross-checks the RPC.

**Monkeypatching:** Use `monkeypatch.setattr(module, "name", value)` for module-level singletons and settings (see `fake_db`).

**What to Mock:**
- External cloud APIs: use `moto` (AWS). Do not hit real AWS.
- Supabase/PostgREST: use `fake_db`.
- Process-global caches and runtime flags: reset via autouse fixtures or `runtime_config.set_flag(..., persist=False)` in tests.

**What NOT to Mock:**
- Pure logic (`SafetyLayer`, `policy_engine`, `cost_estimator`): test the real code with constructed inputs.
- The ML pipeline: `tests/test_ml.py` and `ml/evaluation.py` run the real models on synthetic data (`ml/synthetic/scenarios/*.json`).

## Fixtures and Factories

**Test data:**
- Build rows inline with dict literals and a `NOW = datetime.now(timezone.utc)` constant; timestamps are passed as `.isoformat()`.
- Parameterize with `@pytest.mark.parametrize`, which is used widely (e.g. `("running", True), ("pending", True), ("stopped", False)` for billing state; `url,pooled` for DB pool URL detection; `scenario` for ML scenario JSON files).
- Frontend fixtures are typed object literals (`const base: Action = {...}`) spread-overridden per case: `{ ...base, status: 'approved' }`.

**Location:**
- Shared Python fixtures: `tests/conftest.py`. Fake DB: `tests/fakes.py`.
- ML scenario fixtures: `ml/synthetic/scenarios/` (`idle_ec2.json`, `runaway_lambda.json`, `untagged.json`).
- Cloud pricing fixtures: `data/pricing/`.
- Frontend shared setup: `frontend/src/test/setup.ts`.

## Coverage

**Requirements:** Backend + ML coverage must be at least 70% (`--cov-fail-under=70` in `.github/workflows/ci.yml`). No frontend coverage threshold is enforced.

**View coverage:**

```bash
pytest --cov=backend --cov=ml --cov-report=term-missing
```

A `.coverage` file exists at the repo root from a prior run; it is a generated artifact.

## Test Types

**Unit tests:**
- Pure services and helpers: `test_policy_engine.py`, `test_policy_rules.py`, `test_cost_estimator.py`, `test_safety_layer.py`, `test_features.py`, `test_anomaly_detector.py`.

**Integration tests:**
- `tests/test_integration.py` and `tests/test_endpoints.py` run the FastAPI app through its routes with the `fake_db` and moto. `tests/test_api_surface.py` checks the API surface.
- `tests/test_startup.py` and `tests/test_scheduler.py` cover app lifecycle.
- The CI `test` job is labelled "incl. integration" and runs the full `pytest` suite (no marker split).

**Security/regression-focused tests:** The suite contains explicit guard tests (kill switch, DB-backed flag overriding stale memory, IAM policy shape in `tests/test_iam_policy.py`, secret-leak checks parametrized over `raw,leak` in the telemetry/discovery tests). Keep adding a regression test next to any fixed guard bug.

**E2E tests:** Not used. Frontend has unit/component tests only; no Playwright/Cypress config was found.

**Frontend tests:**
- `api.test.ts`: API client behaviour. `supabase.test.ts`: Supabase client wrapper. `format.test.ts`: formatting helpers. `ui.test.tsx`: components and page helpers (`ConfirmButton`, `ErrorBox`, `availableOps`).
- Mock the Supabase module at the top of the file before importing code under test:

```typescript
vi.mock('../lib/supabase', () => ({ supabase: null, getAccessToken: vi.fn(async () => null) }))
```

## Common Patterns

**Async testing (backend):** `asyncio_mode = auto` means `async def test_...` functions run without a marker. Sync FastAPI handlers are exercised through `TestClient`.

**Error testing:**

```python
assert runner.execute_action("a1") is False
assert _action(fake_db)["status"] == "failed"
assert any(l["event_type"] == "action_blocked" for l in fake_db.rows("audit_logs"))
```

Assert the return value, the persisted status, and the audit trail together.

**Expected failures:** `xfail_strict = true` is set, so an `xfail` test that unexpectedly passes fails the run. Remove the marker when a bug is fixed.

**Strict markers:** `--strict-markers` is on; any custom `@pytest.mark.X` must be registered in `pytest.ini` first.

## Gaps and Cautions

- Frontend has no coverage tooling configured.
- `tests/test_telemetry_demo.py` and `scripts/` demos are large; prefer the focused test files when adding coverage.
- Keep `tests/fakes.py` in sync with the SQL RPCs; a drift there makes tests pass while production fails.

---

*Testing analysis: 2026-10-23*
