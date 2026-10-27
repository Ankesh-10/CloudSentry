---
last_mapped_commit: 7c29211f4ff4cdfd1dad8c9001b1fadd2b22c34f
last_mapped_at: 2026-10-23
---
# Coding Conventions

**Analysis Date:** 2026-10-23

## Naming Patterns

**Files:**
- Python modules are `snake_case.py`, grouped by layer: `backend/app/api/*.py` (routers), `backend/app/services/*.py` (business logic), `backend/app/adapters/*.py` (cloud providers), `backend/app/db/*.py` (DB clients), `backend/app/schemas/models.py` (Pydantic models).
- Tests are `tests/test_<area>.py` (e.g. `tests/test_action_runner.py`, `tests/test_policy_engine.py`). Shared fixtures live in `tests/conftest.py`, the in-memory DB fake in `tests/fakes.py`.
- Migrations are numbered SQL files: `db/migrations/NNN_description.sql` (e.g. `db/migrations/014_complete_verified_action.sql`).
- Frontend React components/pages are `PascalCase.tsx` (under `frontend/src/pages/`, `frontend/src/components/`); non-component modules are `camelCase.ts` (`frontend/src/lib/api.ts`, `frontend/src/hooks/queries.ts`); hooks are `useX.ts` (`frontend/src/hooks/useUrlState.ts`).

**Functions:**
- Python: `snake_case`. Private helpers are prefixed with a single underscore: `_now()`, `_parse_ts()`, `_cloud()`, `_get()` in `backend/app/api/actions.py`.
- TypeScript: `camelCase` for functions; hooks use the `use` prefix (`useDashboardSummary`, `useMutation`-style wrappers in `frontend/src/hooks/queries.ts`).

**Variables:**
- Python: `snake_case`; module-level constants `UPPER_SNAKE_CASE` (`REVERSIBLE`, `VERIFY_TIMEOUT_SECONDS`, `EXPECTED_LIVE_STATE`, `METRIC_MAP` in `backend/app/services/action_runner.py` and `backend/app/services/telemetry.py`).
- Logger per module: `logger = logging.getLogger(__name__)` (used in `backend/app/services/action_runner.py`, `backend/app/adapters/aws.py`, `backend/app/services/telemetry.py`).
- Environment-driven settings are `UPPER_SNAKE_CASE` and map to fields on `settings` in `backend/app/config.py` (e.g. `settings.APPROVAL_TTL_HOURS`).

**Types:**
- Python: Pydantic models suffixed by role: `ResourceBase` -> `Resource`, `AnomalyBase` -> `Anomaly` in `backend/app/schemas/models.py`. Use `Optional[...]` for nullable DB columns (the file's header comment states that every nullable column is `Optional`).
- Type hints are used on public signatures (`def list_actions(...) -> ...`, `Optional[datetime]`, `Dict[str, Any]`); both `typing.Optional` and `X | None` styles appear, but the existing modules predominantly use `typing` generics (`List`, `Dict`, `Optional`, `Literal`).
- TypeScript: interfaces/types in `frontend/src/lib/types.ts`; status unions are `type` aliases (e.g. `ActionStatus`).

## Code Style

**Python formatting:**
- Line length is 120 (`ruff.toml`, `target-version = "py311"`).
- No formatter is enforced in CI; code is hand-formatted but follows PEP 8 spacing and the 120-column limit (some lines run long in tests).
- Double quotes for strings are the dominant style.

**Python linting:**
- Ruff is the only Python linter, configured in `ruff.toml`. It is deliberately narrow: `select = ["E9", "F63", "F7", "F82"]` (syntax errors, invalid comparisons, undefined names). Unused-import (`F401`) and style rules are NOT enforced; the file comment says to widen once the code is clean.
- CI runs `ruff check .` (`.github/workflows/ci.yml`, `lint` job). Run it locally before pushing.
- Ruff is configured to exclude `.venv`, `frontend`, and `node_modules`.

**TypeScript formatting/linting:**
- No ESLint or Prettier config is present in `frontend/`. Style is enforced by the compiler only: `frontend/tsconfig.json` sets `"strict": true`, `"noUnusedLocals": true`, `"noUnusedParameters": true`.
- The frontend style is single quotes, no semicolons, and 2-space indentation (see `frontend/src/test/ui.test.tsx`, `frontend/src/hooks/queries.ts`).
- `npm run typecheck` (`tsc --noEmit`) and `npm run build` are the gates.

## Import Organization

**Python order** (observed in `backend/app/services/action_runner.py`, `backend/app/api/actions.py`, `backend/app/main.py`):
1. Standard library (`logging`, `datetime`, `typing`, `uuid`, `hmac`).
2. Third-party (`fastapi`, `pydantic`, `boto3`, `moto`, `pytest`).
3. First-party, always absolute from the `backend` package: `from backend.app.config import settings`, `from backend.app.services import alerts, runtime_config`.

Groups are separated by a blank line. Imports are not sorted by a tool; keep them alphabetical within each group to match the existing files.

**Path aliases:**
- None on the Python side. Imports are absolute from the repo root (`pytest.ini` sets `pythonpath = .`, so `tests` imports `backend.app...` and `tests.fakes`).
- The frontend uses relative imports (`'../lib/api'`, `'../components/ui'`); no `@/` alias was found.

**Test-only imports:** Tests import `pytest`, `moto.mock_aws`, `boto3` directly and use the project's own fixtures from `tests/conftest.py` by name.

## Error Handling

**API layer (FastAPI routers):**
- Raise `HTTPException(status_code=..., detail="...")` for client-facing errors, e.g. `raise HTTPException(status_code=404, detail="Action not found")` in `backend/app/api/actions.py`.
- Auth is enforced with dependencies, not inline checks: `user: dict = Depends(require_operator)` (see `backend/app/auth.py`). Read endpoints use `require_viewer`.
- Handlers that call the blocking supabase-py client are plain `def` (not `async def`) so FastAPI runs them in its threadpool; the file header comments this.
- Mutating endpoints share a rate limiter: `_mutation_limit = [rate_limit("actions", 30)]` in `backend/app/api/actions.py`.

**Service/adapter layer:**
- Cloud SDK failures are caught, logged, and converted. Pattern from `backend/app/adapters/aws.py`:
  ```python
  try:
      ...boto3 call...
  except Exception as e:
      logger.error("Failed to stop instance %s: %s", instance_id, e)
      return False
  ```
  Adapters report failure through return values (`bool`/empty list) rather than raising, except where a domain exception is defined.
- Domain exceptions are small `Exception`/`RuntimeError` subclasses declared next to their module: `CloudProviderError` (`backend/app/adapters/base.py`), `DiscoveryError` (`backend/app/services/discovery.py`), `AuditWriteError` (`backend/app/services/audit_logger.py`), `SupabaseNotConfigured` (`backend/app/db/supabase_client.py`), `SchemaOutOfDate` (`backend/app/db/schema.py`).
- Audit writes are mandatory for state changes: if the audit row cannot be written, `AuditWriteError` is raised and the action is not treated as successful. Do not swallow `AuditWriteError`.

**Guard-first execution:** Mutations go through `SafetyLayer` and the runtime kill switch before any cloud call (`backend/app/services/action_runner.py`, `backend/app/services/safety_layer.py`). New mutating code must route through the same guards and emit an `action_blocked`-style audit event when refused.

**Frontend:**
- API errors surface as `ApiError` (from `frontend/src/lib/api.ts`). Components render them with `ErrorBox` (`frontend/src/components/ui.tsx`).
- Health/detail endpoints return 503 bodies that are still displayed; callers render `error` alongside stale `data` (comment in `frontend/src/hooks/queries.ts`).

## Logging

**Framework:** Standard library `logging`. Configured centrally in `backend/app/logging_config.py` (`LOG_FORMAT=text|json` via `settings`).

**Patterns:**
- Obtain a module logger with `logger = logging.getLogger(__name__)`.
- Use %-style lazy arguments, not f-strings: `logger.error("Failed to stop instance %s: %s", instance_id, e)`.
- Levels: `error` for failed cloud/DB operations, `warning` for recoverable oddities (`logger.warning("Could not get location for bucket %s", name)`).
- Request correlation IDs come from `backend/app/request_context.py`; do not add your own request-id plumbing.
- Do not log secrets or env values. `print(` is only used in scripts under `scripts/`, not in `backend/app`.

## Comments

**When to comment:**
- Explain *why*, especially for safety or race-related decisions. Examples from `backend/app/services/action_runner.py`: the `STALE_CLAIM_SECONDS` comment explains why stale claims are failed; the `EXPECTED_LIVE_STATE` comment explains that the DB copy may be stale.
- Module headers in `backend/app/api/actions.py` and `backend/app/schemas/models.py` explain non-obvious contracts (sync handlers, nullability mirroring the schema).
- Migration files and `tests/fakes.py` reference the migration number they mirror (`complete_verified_action` mirrors migration 014). Keep those references current when the SQL changes.

**Docstrings:**
- Python uses short triple-quoted docstrings on modules, fixtures, and non-obvious functions (`tests/conftest.py`, `tests/fakes.py`). Not every function has one; do not add boilerplate docstrings to trivial functions.
- TypeScript uses `//` line comments; JSDoc is not used.

## Function Design

**Size:** Keep service methods focused; large orchestration lives in `ActionRunner` (`backend/app/services/action_runner.py`) as multiple small private helpers.

**Parameters:** Prefer explicit keyword-friendly parameters. FastAPI query params use `Query(default=..., ge=..., le=...)` (`limit`, `offset` in `backend/app/api/actions.py`).

**Return values:** Service methods return `bool` for execute-style operations (`execute_action(...) -> bool`) and dicts/lists for data. API handlers return Pydantic response models via `response_model=`.

## Module Design

**Exports:** Modules expose plain module-level functions and classes. Singletons are created lazily via `@lru_cache(maxsize=1)` (e.g. `get_runner()` in `backend/app/api/actions.py`) or a module-level `_client` (see `backend/app/db/supabase_client.py`, patched in tests).

**Barrel files:** `__init__.py` files exist in each backend package but are empty or minimal; import from the concrete module (`backend.app.services.action_runner`), not the package.

**Global state:** Runtime flags are process-global (`backend/app/services/runtime_config.py`) with a DB-backed override. Code that reads kill-switch state must read through `runtime_config`, not cache its own copy.

## Configuration

- All settings come from `backend/app/config.py` (pydantic settings reading `.env`). Add new settings there with a default and reference them as `settings.NAME`.
- Never read `.env` contents when writing code or docs; reference `.env.example` for the variable list.

---

*Convention analysis: 2026-10-23*
