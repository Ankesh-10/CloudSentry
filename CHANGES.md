# Codebase Audit & Required Changes

> **Status (2026-10-03): historical.** This is the 2026-09-19 audit of an earlier
> revision. Its findings have since been addressed (auth and roles, API
> hardening, audit integrity, DB constraints, IAM, action safety, anomaly
> lifecycle, tagging, deploy/startup checks, CI, tests, ML promotion/metadata,
> observability, GCP and multi-region). Line references and the
> docs-vs-implementation matrix below describe the old code; the README is the
> current reference.
>
> **Follow-up review (2026-10-04)** fixed: Auto Scaling/EKS/fleet instances
> could be auto-stopped (and so replaced); `limit_lambda` could raise a tighter
> existing reservation; same-named Lambda/RDS in two regions collided
> (migration 012); approvals/rejections/proposals were not audited; one
> operator could loosen caps alone; the CloudWatch cap counted calls, not billed
> metrics; dependencies had no major-version bounds; an unwritable model disk
> was silent; the idle window was 2 h. Added: approval-needed alerts, a
> policies API, pending-change listing/cancel, resource protection API,
> background discovery with status, `X-Total-Count` on lists.

## Executive Summary

| Field | Value |
|---|---|
| **Repository** | CloudSentry (`C:/Users/Asus/Desktop/code/Project/CloudSentry`) |
| **Audit date** | 2026-09-19 |
| **Reviewer** | Principal / Staff+ architecture review (code + docs vs implementation) |
| **Scope** | Entire backend, ML, DB, infra, tests, scripts, config. Frontend code was excluded from review per request. Frontend *absence* is recorded as a documentation mismatch only. |
| **Overall implementation state** | **Partial prototype, not production-ready.** There is a real FastAPI process, a real boto3 AWS adapter, Supabase schema SQL, and an Isolation Forest module. The documented product — live multi-resource telemetry, closed detect → decide → act → verify → audit loop, JWT-protected API, kill-switch, CI/CD, and deployable Docker/Render image — is **not** what the code does. |

The implementation is an incomplete AWS-only pipeline with stubbed health, incomplete discovery/telemetry, a disconnected action executor, and an API that is largely unauthenticated. Documentation in `md/` describes a much more complete system, but `md/` is gitignored so that documentation is not even part of the versioned repository. `README.md` is empty. `.github/workflows/ci.yml` is empty.

**Do not deploy this backend to a network-accessible host, and do not set `GLOBAL_AUTOMATION_ENABLED=true` / `DRY_RUN_MODE=false`, until P0 items are fixed.**

## Critical Issues

1. Almost all API routes have no authentication, including mutating ones (`POST /resources/discover`, `PATCH /anomalies/{id}`, `PATCH /system/{key}`).
2. The only authenticated router (`/actions`) verifies HS256 JWTs with `SUPABASE_JWT_SECRET` defaulting to `""` and `verify_aud=False`. That secret is not in `.env.example`.
3. `resources.upsert(..., on_conflict="provider_id")` has no UNIQUE constraint on `provider_id`. Discovery cannot persist.
4. The detect → decide → act loop is not closed: PolicyEngine writes `pending` actions; nothing in the scheduler executes or verifies them.
5. There is no working runtime kill-switch. SafetyLayer reads process env, not `system_config`. `POST /system/emergency-stop` does not exist.
6. Docker/Render image build is broken: `backend/Dockerfile` copies `requirements.txt` from a context that does not contain it.
7. If an action *is* executed, `limit_lambda` always sets reserved concurrency to `0` (hard disable).

## High Priority

1. Discovery only maps EC2 (partially Lambda). S3, RDS, and EBS are never synced. Telemetry only collects EC2 CPU/network.
2. IAM policy, adapter methods, and rollback path are inconsistent (missing `DeleteFunctionConcurrency`, tagging incomplete, tags mocked as success).
3. Seed policy includes `delete_ebs_volume`. Action runner does not implement it today, but the policy should not exist.
4. Auth/config split-brain: DB `system_config` vs env `settings`; PATCH config is a no-op for runtime safety.
5. ML pipeline is not the documented IF + EWMA + Z-score cold-start gate. Duplicate metrics will crash `pivot()`.
6. Health endpoint is a stub. Render will keep a broken process “healthy”.
7. CI workflow is empty. Tests do not cover policy, safety, APIs, or the real detector. The integration test does not send a JWT.
8. CORS is `allow_origins=["*"]` with `allow_credentials=True`.
9. Demo scripts cannot provision real AWS (dummy AMI, dummy IAM role) and `shutdown_demo.sh` **terminates** instances.

## Medium Priority

1. Internal docs contradict each other (TimescaleDB vs Supabase PG, Terraform in/out of scope, GCP in/out of scope, JWT JWKS vs HS256).
2. `md/` is gitignored; `README.md` is empty. Collaborators cloning the repo get no setup instructions.
3. Cost estimator, retention job, CloudWatch pagination/rate limits, budget caps, and EWMA are missing or stubbed.
4. APScheduler runs blocking boto3/sync jobs on the asyncio loop. Mixed intervals vs docs.
5. GCP adapter is incomplete and cannot even be instantiated (`remove_function_concurrency` missing).
6. Production `requirements.txt` includes test-only packages (`pytest`, `moto[all]`, `factory-boy`).
7. RLS allows authenticated full-row UPDATEs on actions/policies/config; not column-restricted as documented.

## Low Priority

1. Duplicate imports, unused deps, magic numbers, stub `age_minutes=100`, `structlog` unused.
2. Test layout does not match documented `backend/tests/{unit,integration,ml,safety,cloud}/`.
3. Pricing JSON has no version date; Terraform demo differs from `provision_demo.sh`.
4. Learning notes (`md/learn_backend.md`) refer to `optimizer.py` / `executor.py` which do not exist.

---

# Required Changes

## P0 — Must Fix Before Deployment

- Add JWT auth to every non-health route. Fail closed if `SUPABASE_JWT_SECRET` is empty. Verify audience.
- Add `UNIQUE (provider_id)` (or `(provider_id, resource_type)`) before using `ON CONFLICT`.
- Close the action loop: scheduled execution of `pending` auto-actions, verification polling, and a real emergency-stop that the SafetyLayer honors at runtime.
- Fix Docker build context / `CMD` / model+pricing paths so an image actually starts.
- Do not auto-execute Lambda concurrency=0. Do not ship `delete_ebs_volume` as a seeded action.

## P1 — Must Fix Before Production

- Complete discovery + telemetry for all claimed resource types, or change the product claim.
- Align IAM with actual boto3 calls (including rollback).
- Implement real health checks, structured logging, metric retention, and budget/rate-limit enforcement.
- Replace the integration test’s mocked detector + missing JWT with tests that prove the pipeline.
- Fill CI. Stop committing empty workflow files.
- Restrict CORS. Restrict RLS updates to the columns the dashboard is allowed to change.
- Make SafetyLayer read `system_config` (with env as boot default), and make PATCH/emergency-stop update both.

## P2 — Should Fix

- Resolve PRD vs Tech Stack vs Outline vs code (TimescaleDB, Terraform, GCP, JWT algorithm, job cadence).
- Version `README.md` with actual setup commands. Stop gitignoring `md/` if those files are the spec.
- Implement EWMA / data-point cold-start gate, anomaly dedup, unique metric constraint, NextToken pagination.
- Split test vs runtime Python dependencies. Pin versions.
- Remove or finish GCP. Do not leave an un-instantiable adapter behind a config flag.

## P3 — Nice to Have

- Replace magic numbers with named constants / policy config.
- Date-versioned model files with retention of 5 versions.
- Cost estimator for Lambda/S3/RDS/EBS using actual usage × price, labeled “estimated”.
- Drop unused packages (`factory-boy` if unused, `moto[all]` from the runtime image).

---

# Findings

## [CRITICAL] Most API endpoints are unauthenticated, including writes

**Location:**
`backend/app/main.py:43-49`  
`backend/app/api/resources.py:29-36`  
`backend/app/api/anomalies.py:31-39`  
`backend/app/api/system.py:17-36`  
`backend/app/api/actions.py:10`  
`md/outline.md:169`

**Category:**
Security — Authentication

**What is wrong:**
Documentation states every FastAPI endpoint except `/health` requires `Authorization: Bearer <supabase_jwt>`. Only the actions router attaches `Depends(get_current_user)`. Resources, metrics, anomalies, dashboard, system, and audit-logs are public.

**Evidence:**
```10:10:backend/app/api/actions.py
router = APIRouter(dependencies=[Depends(get_current_user)])
```

`resources.py`, `anomalies.py`, `system.py`, `dashboard.py`, `metrics.py`, and `audit.py` define no auth dependency. `POST /api/v1/resources/discover` runs live AWS discovery and returns `detail=str(e)` on failure. `PATCH /api/v1/system/{key}` updates kill-switch/budget keys with no caller identity.

**Why it matters:**
Anyone who can reach the port can enumerate resources and metrics, mark anomalies resolved, flip DB config, and trigger CloudWatch/EC2 describe calls (cost and rate-limit abuse against the AWS account). This is not a demo-only footgun if the service is bound to `0.0.0.0` (Dockerfile and docker-compose do exactly that).

**How it should be changed:**
Apply a global `Depends(get_current_user)` on the `/api/v1` router, leaving only `GET /api/v1/health` public. Do not leak exception strings. Add tests that unauthenticated writes return 401.

**Priority:**
P0

---

## [CRITICAL] JWT verification can be bypassed or is misconfigured

**Location:**
`backend/app/auth.py:13-20`  
`backend/app/config.py:8`  
`.env.example` (no `SUPABASE_JWT_SECRET`)  
`md/CloudSentry_TechStack.md:636-650` and `:1067`

**Category:**
Security — Authentication

**What is wrong:**
Tokens are decoded with HS256 and `settings.SUPABASE_JWT_SECRET`. That setting defaults to `""`. Audience is not verified. Tech Stack says verification should use Supabase JWKS (RS256) or a project JWT secret that is actually configured.

**Evidence:**
```13:20:backend/app/auth.py
        payload = jwt.decode(
            token, 
            settings.SUPABASE_JWT_SECRET, 
            algorithms=["HS256"],
            options={"verify_aud": False}
        )
```

```8:8:backend/app/config.py
    SUPABASE_JWT_SECRET: str = ""
```

`.env.example` documents `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` but not `SUPABASE_JWT_SECRET`.

**Why it matters:**
If the secret is left default, HMAC-JWT verification is against the empty key. An attacker can mint `Authorization: Bearer` tokens that pass `get_current_user` and hit approve/rollback, which execute cloud mutations when automation is enabled and dry-run is off. If the secret is set incorrectly relative to Supabase (HS256 vs RS256/JWKS), legitimate dashboard logins fail and operators are pushed toward disabling auth.

**How it should be changed:**
Refuse to start if JWT configuration is missing. Verify against Supabase JWKS (`RS256`) or the project JWT secret from the Supabase dashboard, including `aud`. Never default a signing key to empty. Add a negative test: forged token with empty key must 401.

**Priority:**
P0

---

## [CRITICAL] Discovery upsert will fail: no UNIQUE constraint on `provider_id`

**Location:**
`backend/app/services/discovery.py:73-74`  
`db/migrations/001_initial_schema.sql:12-25`  
`db/migrations/002_indexes.sql:10`

**Category:**
Bug — Data integrity / core workflow

**What is wrong:**
Discovery upserts with `on_conflict="provider_id"`. PostgreSQL requires a unique or exclusion constraint matching that target. The schema only creates a non-unique index.

**Evidence:**
```73:74:backend/app/services/discovery.py
        if upsert_payload:
            self.db.table("resources").upsert(upsert_payload, on_conflict="provider_id").execute()
```

```10:10:db/migrations/002_indexes.sql
CREATE INDEX idx_resources_provider_id ON resources (provider_id);
```

`001_initial_schema.sql` defines `provider_id VARCHAR(100) NOT NULL` with no UNIQUE.

**Why it matters:**
The first scheduled discovery cycle (and `POST /resources/discover`) will error at upsert. The `resources` table will not be maintained. Telemetry, anomaly detection, and cost estimation all read `resources`. The entire pipeline is blocked.

**How it should be changed:**
Add `UNIQUE (provider_id)` or, more correctly, `UNIQUE (account_id, provider_id)` (Lambda names and S3 names can theoretically collide across types/accounts). Then keep the upsert.

**Priority:**
P0

---

## [CRITICAL] Detect → decide → act loop is not closed

**Location:**
`backend/app/scheduler.py:22-86`  
`backend/app/services/policy_engine.py:121-128`  
`backend/app/services/action_runner.py:16-52`  
`backend/app/api/actions.py:27-51`  
`md/outline.md:247-256`

**Category:**
Architecture / Correctness

**What is wrong:**
Documented WF-5: LOW/MEDIUM actions with `requires_approval=false` auto-execute; a 2-minute job verifies AWS state. Implemented: PolicyEngine inserts `status="pending"` (or `pending_approval`). The scheduler never calls `ActionRunner`. There is no `POST /actions/{id}/execute`. The only execution trigger is `POST /actions/{id}/approve`, which requires a human and a JWT.

Missing scheduled jobs vs Tech Stack / Outline:

| Documented job | Implemented? |
|---|---|
| Discovery 15 min | Yes |
| Telemetry 5 min | Yes |
| Cost update 1 hour | Yes |
| ML inference 5 min (after telemetry) | No — 10 min, independent |
| Policy evaluation inline after anomaly | No — separate 10 min job |
| Action execution / verification 2 min | **No** |
| ML training daily 02:00 UTC | No — weekly Sunday 02:00 |
| Metric retention daily 03:00 UTC | **No** |

**Evidence:**
`scheduler.py` registers discovery, telemetry, anomaly_detector, policy_engine, cost_estimation, ml_retraining only. `ActionRunner` is imported only from `api/actions.py`.

```121:128:backend/app/services/policy_engine.py
                actions_to_create.append({
                    ...
                    "status": "pending_approval" if matched_policy["requires_approval"] else "pending",
                    "dry_run": settings.DRY_RUN_MODE,
```

**Why it matters:**
The product’s stated purpose is autonomous optimization. As written, anomalies may be recorded and actions may be *proposed*, but AWS is never changed unless a client calls approve. Verification (`verified_at`, real `post_state`) never runs. Rollback can be triggered via API, but the happy path of auto-stop idle EC2 does not exist.

**How it should be changed:**
After SafetyLayer pass: if not `requires_approval` and not dry-run, call `ActionRunner.execute_action`. If dry-run, mark completed with an explicit WOULD-log and do not call AWS. Add a verification job that polls AWS until target state or timeout, then writes `post_state` / `failed`. Add `POST /actions/{id}/execute` for approved HIGH-risk actions.

**Priority:**
P0

---

## [CRITICAL] Kill-switch is not real; emergency-stop endpoint is missing

**Location:**
`backend/app/services/safety_layer.py:17-19`  
`backend/app/services/action_runner.py:50-52`  
`backend/app/api/system.py` (entire file)  
`backend/app/config.py:28-35`  
`db/migrations/004_seed_policies.sql:2-9`  
`md/CloudSentry_PRD.md:316-319`

**Category:**
Security — Authorization / Safety

**What is wrong:**
PRD: `POST /api/v1/system/emergency-stop` sets `GLOBAL_AUTOMATION_ENABLED=false` in the database and blocks in-flight work. Implemented: no such route. `GET/PATCH` live under `/api/v1/system/` and `{key}`, unauthenticated. SafetyLayer and ActionRunner read `settings.GLOBAL_AUTOMATION_ENABLED` from **environment**, loaded once at process start. PATCH of `system_config` does not reload `settings` (the code even comments this).

**Evidence:**
```17:19:backend/app/services/safety_layer.py
        if not settings.GLOBAL_AUTOMATION_ENABLED:
            return False, "GLOBAL_AUTOMATION_ENABLED is False (Kill-switch activated)."
```

```33:34:backend/app/api/system.py
    # Note: In a robust setup, updating a config here would also update the in-memory `settings`
```

No `emergency-stop` symbol exists anywhere in `.py` or `.sql` source (repo grep).

**Why it matters:**
Operators and the dashboard cannot stop automation at runtime. The DB flag and the process flag diverge. If someone later enables automation via env and uses the dashboard “kill switch”, AWS stop/concurrency calls continue. Conversely, PATCH of DB `GLOBAL_AUTOMATION_ENABLED=true` looks like automation is on while SafetyLayer still blocks — false operational picture.

**How it should be changed:**
Single source of truth: `system_config` (env values as initial seed only). Cache with short TTL or listen for updates. Implement `POST /system/emergency-stop` (authenticated) that writes DB + in-memory flag. SafetyLayer and ActionRunner must both consult that flag immediately before any boto3 mutating call. Add a test that PATCH/emergency-stop blocks a subsequent execute in the same process.

**Priority:**
P0

---

## [CRITICAL] Docker / Render image will not build or will not boot as documented

**Location:**
`backend/Dockerfile`  
`docker-compose.yml:4-17`  
`render.yaml:1-8`  
`ml/inference.py:11`  
`backend/app/services/cost_estimator.py:16-17`

**Category:**
Infrastructure / Deployment

**What is wrong:**
`backend/Dockerfile` does `COPY requirements.txt .` then `CMD uvicorn backend.app.main:app`. `docker-compose.yml` and Render use **repository root** as build context (`context: .`, `dockerfilePath: ./backend/Dockerfile`). There is no `requirements.txt` at repo root (only `backend/requirements.txt`). Build fails at COPY.

If the context is changed to `backend/`, `COPY . .` drops `ml/`, `data/`, `models/` and the module path becomes `app.main`, not `backend.app.main`. CMD still fails.

docker-compose bind-mounts models at `/models`, but trainer/inference resolve `ml/../models` → `/app/models`.

**Evidence:**
```12:25:backend/Dockerfile
COPY requirements.txt .
...
COPY . .
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```4:8:docker-compose.yml
    build: 
      context: .
      dockerfile: backend/Dockerfile
```

Git tree: `.env.example`, `backend/requirements.txt`, no root `requirements.txt`.

**Why it matters:**
Documented local Docker and Render production deploys cannot start. Even a patched COPY still has wrong model/pricing paths, so ML and cost estimation silently no-op.

**How it should be changed:**
Pick one layout and make Dockerfile match it. Recommended: context = repo root; `COPY backend/requirements.txt .`; `COPY . .`; install; run as `backend.app.main:app`; mount or copy `ml/`, `data/`, `models/` to the paths the code actually uses. Add a CI docker build step. Run the container as a non-root user.

**Priority:**
P0

---

## [CRITICAL] Lambda “limit” hard-disables the function (concurrency=0)

**Location:**
`backend/app/services/action_runner.py:66-68`  
`md/CloudSentry_PRD.md:264-274`  
`cloud_permissions/aws_iam_policy.json:27-37`

**Category:**
Security / Correctness — destructive cloud action

**What is wrong:**
PRD action spec: set reserved concurrency to a limit (example: 5 or 10), store previous value, verify, rollback by restore. Code always calls `put_function_concurrency(..., 0)`. IAM policy allows `lambda:PutFunctionConcurrency` but **not** `lambda:DeleteFunctionConcurrency`, so documented rollback (`remove_function_concurrency`) is denied on a correctly locked-down account.

**Evidence:**
```66:68:backend/app/services/action_runner.py
                elif action["action_type"] == "limit_lambda":
                    success = self.cloud.limit_function_concurrency(resource["provider_id"], 0)
                    message = "Lambda concurrency limited to 0." if success else "Failed to limit concurrency."
```

**Why it matters:**
Concurrency 0 throttles **all** invocations. That is closer to “turn the function off” than “dampen a runaway”. Combined with a forged JWT or a future auto-execute job, this is production-outage territory. Rollback is also unimplemented at the IAM layer.

**How it should be changed:**
Read current reserved concurrency into `pre_state`. Set a configured positive cap (policy parameter). IAM: add `lambda:GetFunctionConcurrency` and `lambda:DeleteFunctionConcurrency` (or `Put` back to stored value). Never default to 0 without an explicit HIGH-risk policy and approval.

**Priority:**
P0

---

## [HIGH] Discovery does not collect S3, RDS, or EBS; Lambda mapping is a stub

**Location:**
`backend/app/services/discovery.py:87-107`  
`backend/app/adapters/aws.py:33-82`  
`md/CloudSentry_PRD.md:120-131`  
`md/outline.md:18-19`

**Category:**
Documentation mismatch / Incomplete implementation

**What is wrong:**
Adapter methods exist for five resource types. `DiscoveryService.run()` only parses EC2 and Lambda. Lambda rows store `FunctionName` as `provider_id`, `state="available"`, and drop runtime, memory, timeout, region, tags, ARN.

**Evidence:**
```87:107:backend/app/services/discovery.py
        # 1. EC2
        ...
        self._sync_resources(account_id, "ec2", parsed_ec2)
        
        # 2. Lambda (Stubbed mapping for brevity)
        lambda_data = self.cloud_adapter.discover_functions()
        parsed_lambda = [{"id": f["FunctionName"], "name": f["FunctionName"], "state": "available"} for f in lambda_data]
        self._sync_resources(account_id, "lambda", parsed_lambda)
        
        logger.info("Resource Discovery cycle completed.")
```

No calls to `discover_buckets`, `discover_databases`, `discover_volumes`. `_get_or_create_account()` always inserts `provider: "aws"` even if `CLOUD_PROVIDER=gcp`.

**Why it matters:**
Claimed resource coverage is false. Lambda tagging requires an ARN (`tag_resource`); storing the name makes tagging fail. Cost metadata for instance types works for EC2 only because EC2 mapping keeps `InstanceType`.

**How it should be changed:**
Parse all five types into the documented dict shape. Store Lambda ARN as `provider_id` or in `metadata.arn`. Honor `CLOUD_PROVIDER` when creating `cloud_accounts`. Treat missing types as explicit out-of-scope in README if you cut MVP.

**Priority:**
P1

---

## [HIGH] Telemetry only polls three EC2 metrics; no batching >500, no NextToken, no rate limit

**Location:**
`backend/app/services/telemetry.py:36-60`  
`backend/app/adapters/aws.py:84-101`  
`backend/app/config.py:35`  
`md/CloudSentry_PRD.md:140-154`  
`md/learn_aws.md:20-22`

**Category:**
Incomplete implementation / Reliability

**What is wrong:**
Documented metrics include Lambda invocations/duration/errors, S3 size/objects, RDS connections/storage, EBS ops. Code builds CloudWatch queries only for EC2 `CPUUtilization`, `NetworkIn`, `NetworkOut`. `MAX_CW_API_CALLS_PER_HOUR` is never read. `get_metric_data` comment says batch at 500 but does not. CloudWatch `NextToken` is ignored, so truncated results look like “no data”.

**Evidence:**
```36:57:backend/app/services/telemetry.py
            if r["resource_type"] == "ec2":
                for metric in ["CPUUtilization", "NetworkIn", "NetworkOut"]:
                    ...
        if not queries:
            return
```

If the account has only Lambda/S3, `queries` is empty and the job returns without logging a type-coverage warning.

**Why it matters:**
Runaway Lambda, unused volume, and RDS anomalies cannot fire from live data. Empty CloudWatch responses are skipped (good), but pagination loss looks identical. Cost and ML features that depend on those metrics stay at 0.

**How it should be changed:**
Build `MetricDataQuery` lists per documented metric map. Chunk at 500. Loop `NextToken`. Count API calls and skip/defer when over `MAX_CW_API_CALLS_PER_HOUR`. Do not insert synthetic zeros.

**Priority:**
P1

---

## [HIGH] Apply-tags is mocked as success; AWS tagging is incomplete

**Location:**
`backend/app/services/action_runner.py:69-71`  
`backend/app/adapters/aws.py:138-149`

**Category:**
Bug / Audit integrity

**What is wrong:**
`apply_tags` in ActionRunner sets `success = True` with comment “Mocking tag apply for now” and never calls the adapter. The adapter implements EC2/EBS `create_tags` and Lambda `tag_resource` only (Lambda needs ARN). S3/RDS are no-ops that still `return True` after the `if/elif`.

**Evidence:**
```69:71:backend/app/services/action_runner.py
                elif action["action_type"] == "apply_tags":
                    success = True # Mocking tag apply for now
                    message = "Tags applied."
```

**Why it matters:**
Audit log and `optimization_actions.status=completed` will claim tags were applied. Operators will believe untagged-resource remediation happened. It did not.

**How it should be changed:**
Call `cloud.apply_tags` with real tag payload from the policy. Return False for unsupported types. Verify with describe/get-tags before marking completed.

**Priority:**
P1

---

## [HIGH] Seeded HIGH-risk policy is `delete_ebs_volume`

**Location:**
`db/migrations/004_seed_policies.sql:36-45`  
`md/CloudSentry_PRD.md:58-65`  
`md/outline.md:44-45`

**Category:**
Security — destructive operations

**What is wrong:**
PRD/Outline: automatic resource deletion is a non-goal; unused volume is V2; deletion is never auto-executed. Seed data inserts an enabled policy whose `action_type` is `delete_ebs_volume`. ActionRunner treats unknown types as failed (currently). There is still no architectural guarantee against implementing it later, and PolicyEngine will propose those actions if an `unused_volume` anomaly appears.

**Evidence:**
```36:45:db/migrations/004_seed_policies.sql
    'Require approval for unused EBS volume deletion',
    ...
    'delete_ebs_volume',
    'HIGH',
    true,
```

`action_runner.py` has no `delete_volume` call (absence verified by repo grep except this SQL).

**Why it matters:**
A one-line ActionRunner addition would make deletion reachable via the approval API. Seed policies should not advertise a forbidden action.

**How it should be changed:**
Replace with tag + recommend only, or omit until a dedicated approval flow exists that cannot be confused with auto-execute. Keep `ec2:DeleteVolume` out of IAM (it is already absent — keep it that way).

**Priority:**
P1

---

## [HIGH] Health check is a stub; Render will not detect backend failure

**Location:**
`backend/app/main.py:51-60`  
`render.yaml:8`  
`md/CloudSentry_PRD.md:926-935`

**Category:**
Reliability / Observability

**What is wrong:**
`GET /api/v1/health` always returns HTTP 200 with `"db": "pending"`, `"ml_model": "pending"`, `"aws_connectivity": "pending"`, etc. It does not ping asyncpg, Supabase, boto3, or telemetry freshness. Render `healthCheckPath: /api/v1/health` will keep routing traffic to a process whose pool failed to initialize (`init_db_pool` swallows exceptions and logs).

**Evidence:**
```51:60:backend/app/main.py
@app.get("/api/v1/health", tags=["system"])
async def health_check():
    return {
        "status": "ok",
        "db": "pending",
        ...
    }
```

```18:31:backend/app/db/asyncpg_pool.py
        logger.warning("DATABASE_URL not configured. Time-series queries will fail.")
        return
    ...
    except Exception as e:
        logger.error(f"Failed to initialize asyncpg pool: {e}")
```

**Why it matters:**
APScheduler jobs then throw `RuntimeError: Database pool is not initialized` every cycle. The load balancer still reports healthy. Silent data freeze.

**How it should be changed:**
Probe pool (`SELECT 1`), Supabase (`system_config` read), optional STS/CloudWatch, last telemetry timestamp, model file presence, automation flags. Return 503 if DB is down. Do not require AWS success to pass liveness if you split liveness vs readiness.

**Priority:**
P1

---

## [HIGH] CORS allows any origin with credentials

**Location:**
`backend/app/main.py:35-41`  
`md/outline.md:649`

**Category:**
Security — APIs

**What is wrong:**
```35:41:backend/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Outline required explicit Vercel + localhost origins.

**Why it matters:**
With unauthenticated GET/PATCH (Finding 1), a browser session on any site can call the API. `allow_origins=["*"]` + `allow_credentials=True` is also spec-invalid; browsers may omit CORS headers and “fix” it by disabling credentialed calls, which then breaks a real dashboard.

**How it should be changed:**
Env-driven allowlist. Never `*` with credentials.

**Priority:**
P1

---

## [HIGH] Safety and budget controls documented as enforced are not implemented

**Location:**
`backend/app/services/safety_layer.py`  
`backend/app/config.py:28-35`  
`md/CloudSentry_PRD.md:300-313`  
`md/CloudSentry_PRD.md:800-802`

**Category:**
Security / Incomplete implementation

**What is wrong:**
Implemented: env kill-switch, `protected` column, tag `cloudsentry:protected=true`, daily count of non-dry-run actions, per-resource cooldown.

Not implemented: `MAX_MONTHLY_BUDGET_USD`, `MAX_DAILY_SPEND_USD`, `MAX_CW_API_CALLS_PER_HOUR`, hardcoded resource denylist, `do-not-stop` tag (PRD Action: Stop Idle EC2), `cloudsentry:exempt=true` policy skip, “RDS always requires approval”, duplicate-action check beyond pending/approved/executing on the **same anomaly** (a new anomaly on the same instance can still enqueue another stop).

Daily limit query does not filter `status` — dry-run false rows that failed still consume the budget, but dry-run true rows do not. Cooldown counts dry-run proposals, which can block later real actions.

**Why it matters:**
The kill-switch story is the main reason this tool is allowed to call `StopInstances`. Half the advertised brakes are dead code (config present, never read).

**How it should be changed:**
Implement each advertised check with tests. Read spend from `cost_records` (or fail closed if cost job has not run). Exempt tag short-circuits PolicyEngine before SafetyLayer.

**Priority:**
P1

---

## [HIGH] Policy engine logic does not match documented policies

**Location:**
`backend/app/services/policy_engine.py:58-105`  
`db/migrations/004_seed_policies.sql:13-24`  
`backend/app/services/anomaly_detector.py:54-56`  
`md/CloudSentry_PRD.md:792-802`

**Category:**
Bug / Correctness

**What is wrong:**
1. Policies are loaded `order("priority", desc=True)`. Docs: ascending = higher priority (lower number wins).
2. Empty/unparseable `conditions` match everything (`if not conditions or self._evaluate_condition`). Bare `except:` on JSON parse.
3. `idle_score` is taken from `features_snapshot.rolling_avg_cpu_24h`, default **100**. Idle anomalies are recorded with `features={}` (`anomaly_detector.py` passes `{}`), so idle_score becomes 100 and always satisfies `gte 0.80`. If features were populated with CPU%, *lower* CPU would fail `gte 0.80` (inverted vs “idle score”).
4. `resource.age_minutes` is hardcoded `100`.
5. No `system.automation_enabled` / `system.daily_action_count` in the condition context (those live only in SafetyLayer, and from env).
6. `runaway_lambda` policy exists; detector never emits `anomaly_type="runaway_lambda"` (it emits `ml_behavioral_anomaly` or `unusual_cpu_spike`). Untagged policy is documented as MVP; not seeded and not detected.

**Evidence:**
```79:86:backend/app/services/policy_engine.py
                    "idle_score": anomaly["features_snapshot"].get("rolling_avg_cpu_24h", 100) if anomaly["anomaly_type"] == "idle_compute" else 0.0
                },
                "resource": {
                    ...
                    "age_minutes": 100 # Stub for MVP
```

```54:56:backend/app/services/anomaly_detector.py
            if idle_result["is_anomaly"]:
                self._record_anomaly(resource_id, idle_result, now, {})
                continue
```

**Why it matters:**
Idle EC2 will either always match (empty snapshot) or never match (CPU% used as score). Runaway Lambda policy never fires. Priority ordering is reversed when multiple policies exist.

**How it should be changed:**
Compute a real idle_score in `[0,1]`. Pass the feature snapshot into the anomaly row. Emit documented `anomaly_type` values from rule logic after the ML score. Order policies `priority ASC`. Treat parse failure as no-match, not match-all. Remove `age_minutes` stub; use `first_seen`.

**Priority:**
P1

---

## [HIGH] ML pipeline is not the documented Isolation Forest system

**Location:**
`ml/trainer.py:26-31`  
`ml/inference.py:35-47`  
`ml/baseline.py:5-11,37-46`  
`backend/app/services/anomaly_detector.py:63-80`  
`backend/app/services/ml_retrainer.py:37-49`  
`backend/app/config.py:26`  
`ml/evaluation.py:54-57`  
`md/outline.md:281-325`

**Category:**
ML / Documentation mismatch

**What is wrong:**
Documented: cold-start gate by data-point count (Z-score <144, EWMA <2016, else IF); contamination 0.05; threshold -0.3; daily train on 30 days; versioned `if_{type}_{date}.pkl` retain 5; EWMA implemented; idle = CPU<5% and network<1MB/hr for >2h.

Implemented:
- Z-score fallback only if `predict()` returns reason `"Model not trained"`, not based on sample count. If a `.pkl` exists, IF is used even on 12 points.
- **No EWMA code** (repo grep for `ewma`/`EWMA` in `.py`: none).
- `IsolationForest(contamination=0.001)` vs documented 0.05.
- Config default threshold `-0.60`; `.env.example` says `-0.3`.
- Retrain weekly, last **7** days, skip if `<500` raw rows (docs: 2016 / 30 days).
- Models saved only as `if_{type}_latest.pkl`. No rotation.
- Idle rule: 24h of points and `max(CPU) < 2%`. No network condition. Window is 24h not 2h.
- Anomaly types written: `idle_compute`, `unusual_cpu_spike`, `ml_behavioral_anomaly` — not the documented taxonomy.
- Evaluation uses `model.predict()` (contamination labels) while production uses `score_samples()` + threshold. Offline metrics do not measure production behavior.
- `hours_unattached` and `estimated_cost_per_hour` are always `0.0` in feature vectors.
- Training on the evaluation script’s first N rows and scoring the full set including train rows is leakage relative to the claimed precision/recall targets (those targets are still TBD).

**Evidence:**
```26:31:ml/trainer.py
        model = IsolationForest(
            n_estimators=100,
            contamination=0.001,
            random_state=42,
            n_jobs=-1
        )
```

```63:74:backend/app/services/anomaly_detector.py
            ml_result = self.inference_engine.predict(features_df, resource_type)
            
            if ml_result["reason"] == "Model not trained":
                # Fallback to Baseline Z-score
                if resource_type == 'ec2':
                    baseline_result = detect_anomaly_zscore(df, 'CPUUtilization')
```

Z-score threshold in code is `3.0`; docs say `2.5`.

**Why it matters:**
Calling this “ML-driven cost anomaly detection with cold-start fallback” overstates a heuristic idle-CPU rule plus an IF model that may not match training-time labels. Duplicate/zero-filled features make IF scores uninterpretable.

**How it should be changed:**
Implement the documented gate, contamination, threshold (one source of truth), EWMA or drop it from docs, versioned artifacts, and evaluate with the **same** `score_samples` path used in production. Do not train and score on the same rows when reporting precision.

**Priority:**
P1

---

## [HIGH] Duplicate CloudWatch inserts will crash feature engineering

**Location:**
`backend/app/services/telemetry.py:71-82`  
`db/migrations/001_initial_schema.sql:28-35`  
`ml/features.py:15`

**Category:**
Bug / Reliability

**What is wrong:**
`resource_metrics` has no unique key on `(resource_id, time, metric_name)`. Each 5-minute job requests a 5-minute window; CloudWatch timestamps overlap across runs. `copy_records_to_table` appends duplicates. `DataFrame.pivot(...)` raises on duplicate index/column pairs. `AnomalyDetectorService.run()` has no per-resource try/except, so one resource aborts the whole job.

**Why it matters:**
After a few successful telemetry cycles, ML inference can start throwing every 10 minutes. Anomaly detection dies while `/health` still says ok.

**How it should be changed:**
`UNIQUE (resource_id, time, metric_name)` plus `ON CONFLICT DO NOTHING`, or idempotent upsert. Catch pivot errors per resource. Dedup in SQL (`DISTINCT ON`) before pandas.

**Priority:**
P1

---

## [HIGH] CI is an empty file; test suite does not prove the system

**Location:**
`.github/workflows/ci.yml` (0 bytes, tracked)  
`tests/test_aws_adapter.py`  
`tests/test_features.py`  
`tests/test_integration.py`  
`md/outline.md:560-623`  
`md/CloudSentry_TechStack.md:844-867`

**Category:**
Testing / CI/CD

**What is wrong:**
GitHub Actions workflow is empty. Documented tests (`test_policy_engine`, `test_safety_layer`, `test_cost_estimator`, `test_isolation_forest`, `test_emergency_stop`, `@pytest.mark.cloud`) do not exist. Layout is `tests/` at repo root, not `backend/tests/{unit,integration,...}`.

`test_integration.py` patches `AnomalyDetectorService.run` and **inserts a fake anomaly**, then calls `POST /actions/{id}/approve` **without an Authorization header** while the actions router requires JWT. That test cannot pass against the current app. It also mutates `settings.GLOBAL_AUTOMATION_ENABLED` in-process.

`test_aws_adapter.py` covers discover+stop EC2 only. `test_features.py` covers one EC2 vector and empty input.

**Evidence:**
`.github/workflows/ci.yml` file length 0.  
`test_integration.py:214-241` patches the detector and posts approve with no bearer token.

**Why it matters:**
“Green locally” can mean “mocks returned mocks”. There is no CI gate. Safety properties (kill-switch, dry-run, no delete) are untested.

**How it should be changed:**
Write the workflow from Tech Stack §16 (ruff + pytest unit/ml; skip cloud). Add unit tests for policy, safety, cost, auth 401s, unique upsert. Fix integration: mock JWKS/secret, do not skip the detector, or mark it as a service-level test with a real local Supabase. Keep moto for AWS.

**Priority:**
P1

---

## [HIGH] IAM policy does not match code paths or PRD least-privilege spec

**Location:**
`cloud_permissions/aws_iam_policy.json`  
`md/CloudSentry_PRD.md:835-874`  
`backend/app/adapters/aws.py`

**Category:**
Cloud / Security

**What is wrong:**
PRD policy includes `s3:GetBucketLocation`, `lambda:DeleteFunctionConcurrency`, `ec2:DeleteTags`, `rds:Stop/Start`, region condition `us-east-1`, `ce:GetCostAndUsage` (V2). Committed policy is narrower and also **wrong for rollback and tagging**:

- Missing `lambda:DeleteFunctionConcurrency` (rollback).
- Missing `s3:GetBucketLocation` / tagging.
- `CreateTags` resource list is instance and Lambda ARNs only — EBS tagging denied.
- No `lambda:GetFunction` / `GetFunctionConcurrency`.
- Stop/Start not region-conditioned as in PRD.
- Discovery uses `list_buckets` (`s3:ListAllMyBuckets` is present — OK) but never GetBucketLocation.

**Why it matters:**
Least privilege is good; incomplete privilege makes documented rollback fail with AccessDenied, which ActionRunner treats as `success=False` without triggering the PRD “rollback if pre_state stored” behavior (that automatic rollback-on-failure is also not implemented).

**How it should be changed:**
Generate IAM from the actual adapter methods. Add only APIs you call. Keep Terminate/DeleteBucket/DeleteDBInstance/DeleteVolume denied. Add region condition.

**Priority:**
P1

---

## [HIGH] Demo provision/shutdown scripts are unsafe and will not work on real AWS

**Location:**
`scripts/provision_demo.sh`  
`scripts/shutdown_demo.sh`  
`scripts/run_stress.sh`  
`terraform/main.tf`  
`md/outline.md:441`  
`md/CloudSentry_TechStack.md:1030`

**Category:**
Cloud / Reliability / Safety

**What is wrong:**
- `provision_demo.sh` uses AMI `ami-12c6146b` (moto dummy) and Lambda role `arn:aws:iam::123456789012:role/dummy-role`. On a real account this fails or launches garbage. It creates **3** t2.micro instances (PRD: 1). Lambda errors are swallowed.
- `shutdown_demo.sh` calls **`terminate-instances`**, not stop. PRD demo shutdown is stop to avoid destroy. Terminate is irreversible for instance store and more destructive than the product allows itself via IAM (Terminate is correctly absent from the agent policy — this script uses the operator’s broader CLI creds).
- `run_stress.sh` `PutMetricData` into `AWS/EC2`. Custom datapoints in that namespace are not the same as instance standard metrics; `GetMetricData` for `AWS/EC2` + `InstanceId` typically still reads AWS-published metrics. The script likely does **not** trigger the detector on a real account. `scripts/inject_anomaly.py` inserts 12 CPU points; idle rule needs 288; IF is used if a pkl exists, so Z-score path may never run.
- Terraform (explicitly “NOT USED” in Tech Stack §19 and Outline §2) creates 2 EC2 (one `t3.micro`, not classic free-tier), orphan EBS, S3 — no Lambda/RDS. Two competing demo definitions.

**Why it matters:**
Operators following scripts can terminate the wrong instances if `.demo_instances` is stale, or burn money on t3.micro + extra volumes. Demo rehearsal as documented cannot succeed.

**How it should be changed:**
One provisioning path. Real AMI data source (as Terraform already does). Stop, don’t terminate, unless tagged `ManagedBy=CloudSentry-Demo`. Inject anomalies via `inject_anomaly.py` into `resource_metrics` with enough history, or run OS `stress` on the instance as PRD §20 describes.

**Priority:**
P1

---

## [HIGH] `.env.example` contains a real Supabase project URL and a full anon JWT

**Location:**
`.env.example:2-3`

**Category:**
Secrets / Repository hygiene

**What is wrong:**
The example file is tracked and contains `https://wqaebwpsrijmmezrfubo.supabase.co` and a complete three-segment JWT labeled as `SUPABASE_ANON_KEY`. Tech Stack’s own example used placeholders (`your-project`, `eyJ...`). Anon keys are “public” only if RLS is correct; this still pins a live project in git forever.

**Why it matters:**
Project ref leak + anon key lets anyone hit PostgREST. Current RLS denies `anon` SELECT (policies are `TO authenticated` only), so table dumps may fail — but Auth endpoints, error messages, and future RLS mistakes are exposed. Rotate if this project is real. Replace with placeholders.

**How it should be changed:**
Redact to placeholders. `git rm --cached` is not enough for history; rotate Supabase anon/service keys if they were ever committed in full. Add `gitleaks` as Outline §12 recommended (not present).

**Priority:**
P1

---

## [HIGH] Exception details leaked; many failures swallowed as empty success

**Location:**
`backend/app/api/resources.py:35-36`  
`backend/app/adapters/aws.py:31,41-43,98-101`  
`backend/app/db/asyncpg_pool.py:30-31`  
`backend/app/services/audit_logger.py:34-37`

**Category:**
Reliability / Security

**What is wrong:**
Discover returns `HTTP 500 detail=str(e)` (internal errors, possibly AWS messages). Adapter methods catch `ClientError`, log, and return `[]`/`False`. Callers treat empty lists as “no resources” (account looks empty during an outage). Audit log insert failure is only logged — the mutating AWS call may already have happened.

**Why it matters:**
Provider outage → discovery writes nothing new, last_seen goes stale, but resources remain `running` in DB; telemetry keeps targeting them. Cost/anomaly views look “healthy/idle”. Failed audit insert after `stop_instances` violates the “audit before API call” requirement (audit is written, but if *that* fails the AWS call still proceeds in ActionRunner — actually ActionRunner writes audit *then* calls AWS; if audit throws, execute might still continue after log_action’s inner try/except).

**How it should be changed:**
Distinguish “zero resources” from “provider error”. Fail the job, surface on `/health`, do not upsert. If audit write fails, **do not** call mutating APIs. Return generic 500 to clients.

**Priority:**
P1

---

## [MEDIUM] Documented architecture does not match running architecture

**Location:**
`md/CloudSentry_PRD.md:329-376` (TimescaleDB, SQLAlchemy)  
`md/CloudSentry_TechStack.md:148-175` (Supabase PG, no SQLAlchemy)  
`md/outline.md:36-45` (GCP out of scope, Terraform out of scope)  
`backend/app/adapters/gcp.py`  
`terraform/main.tf`  
`backend/app/main.py`

**Category:**
Documentation / Architecture

**What is wrong:**
Three specs disagree, and the code is a fourth variant:

| Claim | PRD | Outline / Tech Stack | Code |
|---|---|---|---|
| Metrics DB | PostgreSQL + TimescaleDB hypertable, SQLAlchemy | Supabase PG, no TimescaleDB, no SQLAlchemy | Supabase + asyncpg, no hypertable, no SQLAlchemy |
| GCP | Stub in V2 | Out of scope MVP | `GCPAdapter` + `CLOUD_PROVIDER` + GCP IAM YAML |
| Terraform | “NOT USED” | Out of scope | `terraform/*.tf` present |
| JWT | HS256 | JWKS / verify algorithm TBD | HS256 empty secret |
| Frontend | React 18 dashboard | `frontend/` tree | **No `frontend/` in git** |
| Logging | structlog JSON | structlog | `logging.basicConfig` |
| Cost Explorer | V2 | V2 | Not present (correct) |

GCPAdapter is missing `remove_function_concurrency`, so `get_cloud_adapter()` raises `TypeError` at import of DiscoveryService if `CLOUD_PROVIDER=gcp` — scheduler module instantiates services at import time, which takes down the API.

**Why it matters:**
New engineers (or future you) will implement the wrong system. `CLOUD_PROVIDER=gcp` is a footgun for process boot.

**How it should be changed:**
Pick Tech Stack + Outline as source of truth, patch PRD, or vice versa. Delete or quarantine Terraform/GCP until scheduled. Lazy-import adapters. Make `GCPAdapter` abstract-complete or remove it.

**Priority:**
P2

---

## [MEDIUM] README empty; product docs gitignored; learning notes stale

**Location:**
`README.md` (0 bytes, tracked)  
`.gitignore:168` (`md/`)  
`md/CloudSentry_PRD.md`  
`md/learn_backend.md:20-21`

**Category:**
Documentation

**What is wrong:**
A clone of origin has no README instructions and no PRD/outline (ignored). `learn_backend.md` says services include `optimizer.py` and `executor.py`. Those files are not in the tree (historical commit message “Optimizer and executor service” exists; current names are `policy_engine.py` / `action_runner.py`).

**Why it matters:**
Setup instructions in Outline (`uvicorn backend.app.main:app`, `supabase db push`) are invisible to anyone who only has git. Empty README is a production-readiness fail by itself.

**How it should be changed:**
Write README from *actual* commands. Either stop ignoring `md/` or publish a short architecture doc that is tracked. Delete or rewrite learn_* files.

**Priority:**
P2

---

## [MEDIUM] API surface does not match the spec (paths, payloads, pagination)

**Location:**
`backend/app/api/*.py`  
`md/outline.md:173-194`

**Category:**
API / Documentation mismatch

**What is wrong:**

| Spec | Code |
|---|---|
| `GET /dashboard/overview` | `GET /dashboard/summary` with different fields (no automation/dry_run/total_cost) |
| `GET /dashboard/cost-trend` | Missing |
| `GET /dashboard/anomaly-summary` | Missing |
| `GET /system/config` | `GET /system/` |
| `PATCH /system/config` `{key,value}` | `PATCH /system/{key}` `{value}` |
| `POST /system/emergency-stop` | Missing |
| `PATCH /anomalies/{id}/status` | `PATCH /anomalies/{id}` |
| `GET /actions/{id}` | Missing |
| `POST /actions/{id}/execute` | Missing |
| `GET /resources/{id}` includes 24h metrics | Returns empty lists |
| `GET /metrics/{id}?from&to` | `days` query ignored; last 1000 rows |
| Approve body | `{approved, user_id}` — **client-supplied actor** |
| Rollback `user_id` | Query default `"API_USER"` |
| Pagination | Audit only (`limit`/`offset`); resources/anomalies unbounded |

`ActionApproval.user_id` is taken from the JSON body, not the JWT `sub`. Even with auth, any logged-in user can attribute approval to another identity.

**How it should be changed:**
Match the outline or change the outline. Stamp `approved_by` from the token. Cap list endpoints.

**Priority:**
P2

---

## [MEDIUM] Cost estimator is a stub and will unbounded-grow `cost_records`

**Location:**
`backend/app/services/cost_estimator.py:30-62`  
`data/pricing/aws_us_east_1.json`  
`md/CloudSentry_PRD.md:169-176`

**Category:**
Correctness / Scalability (future)

**What is wrong:**
Loads region JSON (good). For EC2 only, writes `estimated_cost_usd = hourly rate` with no hours, no billing period, no Lambda/S3/RDS/EBS despite JSON containing those prices. Job runs hourly and **inserts** a new row every time. No 90-day cleanup job exists for metrics or costs (documented APScheduler cron at 03:00).

**Why it matters:**
Dashboard “savings” and “total cost” cannot be correct (`estimated_savings_usd` is never populated when creating actions). Table growth is O(resources × hours) with no retention. **CURRENT BUG:** costs are wrong. **FUTURE SCALABILITY:** unbounded inserts.

**How it should be changed:**
Compute period cost from utilization × price. Upsert by `(resource_id, billing_period_start)`. Label source `estimated`. Add the DELETE retention job.

**Priority:**
P2

---

## [MEDIUM] APScheduler blocks the API event loop; jobs are independent of documented order

**Location:**
`backend/app/scheduler.py`  
`backend/app/services/discovery.py:79` (`def run` sync)  
`backend/app/services/telemetry.py:15` (`async def run`)  
`backend/app/main.py:16-25`

**Category:**
Performance / Reliability

**What is wrong:**
`AsyncIOScheduler` runs sync `discovery_service.run` / `policy_engine.evaluate_all` / `cost_estimation_service.run` on the event loop. Those functions do blocking boto3 and HTTP supabase-py. Telemetry/ML are async but still call sync supabase-py inside. Inference is not chained after telemetry (could run on empty new data). Duplicate `from backend.app.scheduler import scheduler` in `main.py`.

Uvicorn Dockerfile uses a single worker (good — multiple workers would double-schedule jobs). That single-worker constraint is undocumented; scaling Render instances would duplicate every AWS poll and every potential action.

**Why it matters:**
API latency spikes every 15 minutes during discovery. Two Render instances would double CloudWatch spend and could double-create actions (no distributed lock).

**How it should be changed:**
`run_in_executor` for boto3, or a dedicated worker process with a DB advisory lock. Document replica count = 1 until then. Sequence telemetry → inference in one job.

**Priority:**
P2

---

## [MEDIUM] RLS is coarser than documented; no Realtime publication in migrations

**Location:**
`db/migrations/003_rls_policies.sql`  
`md/CloudSentry_TechStack.md:245-261`

**Category:**
Security — Authorization / Database

**What is wrong:**
Documented: authenticated may UPDATE only `anomalies.status`, `optimization_actions.status` to `approved`, etc. SQL: `FOR UPDATE TO authenticated USING (true) WITH CHECK (true)` on anomalies, optimization_actions, **policies**, and **system_config`. A dashboard user using PostgREST + user JWT can rewrite policy JSON to `delete_ebs_volume` / `requires_approval=false`, or flip config rows.

No migration enables Realtime (`ALTER PUBLICATION supabase_realtime`, replica identity). Outline WF-8 will not receive INSERT events unless configured out of band.

**How it should be changed:**
Column-level grants or `WITH CHECK` that only allows status transitions. Do not allow authenticated UPDATE on `policies`. Add Realtime publication if the dashboard needs it; otherwise drop the claim.

**Priority:**
P2

---

## [MEDIUM] No input validation / rate limiting / timeouts on HTTP or cloud calls

**Location:**
`backend/app/api/anomalies.py:31-34`  
`backend/app/api/system.py:19-27`  
`backend/app/api/metrics.py:11-24`  
`backend/app/db/asyncpg_pool.py:23-28`

**Category:**
Security / Reliability

**What is wrong:**
Anomaly status is allowlisted (good). System keys are allowlisted (good) but values are unconstrained strings (`GLOBAL_AUTOMATION_ENABLED=maybe`). Resource IDs in metrics are not UUID-validated. No FastAPI rate limit (Outline: deferred). supabase-py has no explicit timeout. asyncpg `command_timeout=60` only. boto3 retries exist (good, adaptive 3). No request body size limit.

**How it should be changed:**
Pydantic enums for booleans/status. UUID path params. SlowAPI or gateway rate limits on discover/approve. HTTPX timeout for Supabase.

**Priority:**
P2

---

## [MEDIUM] Runtime image includes test dependencies; structlog unused; factory-boy unused

**Location:**
`backend/requirements.txt`  
`backend/app/main.py:1-10`

**Category:**
Dependencies

**What is wrong:**
`pytest`, `pytest-asyncio`, `moto[all]`, `pytest-mock`, `factory-boy` install into the Docker image. `moto[all]` is large. `structlog` is listed but the app uses stdlib logging. `factory-boy` has no imports in tests. `google-cloud-*` pulled in for a non-MVP adapter. Unpinned `>=` ranges (reproducibility).

**How it should be changed:**
`requirements.txt` vs `requirements-dev.txt`. Pin hashes or at least `~=` . Use structlog or drop it.

**Priority:**
P2

---

## [MEDIUM] Anomalies are not deduplicated; model_version is guessed from reason text

**Location:**
`backend/app/services/anomaly_detector.py:84-97`

**Category:**
Correctness

**What is wrong:**
Every 10-minute cycle inserts a new `anomalies` row if idle still holds. PolicyEngine skips only if that **anomaly id** already has a pending action — new rows generate new actions once cooldown expires. `model_version` is `"baseline"` if reason contains `"zscore"` or `"idle"`, else `"if_latest"` — not the filename/date.

**How it should be changed:**
Upsert active anomaly per `(resource_id, anomaly_type)` or suppress if an active one exists. Write the actual pkl name / `zscore_fallback`.

**Priority:**
P2

---

## [MEDIUM] `get_metric_data` ABC signature mismatch; AWS clients can be None

**Location:**
`backend/app/adapters/base.py:30-31`  
`backend/app/adapters/aws.py:15-31,84`  
`backend/app/adapters/gcp.py:111-115`

**Category:**
Code quality / Reliability

**What is wrong:**
ABC: `get_metric_data(self, queries)`. AWS/GCP: extra `start_time, end_time`. Works in Python, lies to implementers. AWS `__init__` catches all exceptions and leaves instance without clients → AttributeError later, caught as empty discover. GCP missing abstract `remove_function_concurrency`.

**Priority:**
P2

---

## [LOW] Magic numbers, stubs, duplicate imports, unused query params

**Location:**
Various: `age_minutes = 100`; idle `2.0%` / `288`; Z-score `3.0`; IF `contamination=0.001`; `ML_ANOMALY_THRESHOLD` dual defaults; `main.py` double-import of scheduler; `metrics.py` `days` unused; cost `inst_type` default `t2.micro`.

**Category:**
Maintainability

**What is wrong:**
These are not style nits; they encode product thresholds in scattered literals that already disagree with the PRD. Operators cannot tune idle CPU without a code change (policies in DB are ignored for the rule in `check_idle_compute`).

**How it should be changed:**
Centralize thresholds in `system_config` / settings. Remove dead parameters or use them.

**Priority:**
P3

---

## [LOW] Repository hygiene

**Location:**
`.gitignore`  
Git tree vs local workspace

**Category:**
Git / Repository hygiene

**What is wrong:**
- `.env` is ignored (good). `.env.example` is tracked with live-looking values (see HIGH).
- `models/*.pkl` ignored (good). Local `models/if_*_latest.pkl` exist; Docker path may not see them.
- `md/` ignored — specifications not versioned.
- `.github/workflows/ci.yml` tracked while empty.
- `graphify-out/` and `pytest-cache-files-*` appeared locally; not in `git ls-files` (good if they stay untracked). Add `graphify-out/` to `.gitignore`.
- No `.dockerignore` — build context may upload `.venv`, `.git`, local `.env` if someone “fixes” COPY with `COPY . .` from repo root. **That would bake secrets into an image layer.**

**How it should be changed:**
Add `.dockerignore` with `.env`, `.venv`, `md/` if needed, `.git`. Do not copy `.env` into images. Ignore `graphify-out/`.

**Priority:**
P3 (dockerignore is P0-adjacent if you fix Dockerfile `COPY . .` — treat ignore rules as part of the Docker P0 fix)

---

# Documentation vs Implementation Matrix

| Documented Feature | Implemented? | Evidence | Status | Required Change |
|---|---|---|---|---|
| Discover EC2 | Partial | `discovery.py` maps instances | REAL for EC2 describe | Unique constraint; tags/metadata OK |
| Discover Lambda | Partial | Stub mapping, name not ARN | PARTIAL | Full fields + ARN |
| Discover S3 | No | Adapter only, never called | PLACEHOLDER | Call + location/tags |
| Discover RDS | No | Adapter only | PLACEHOLDER | Call + map status |
| Discover EBS | No | Adapter only | PLACEHOLDER | Call + attachment |
| CloudWatch telemetry (10 metrics, 5 min) | Partial | 3 EC2 metrics only | PARTIAL | Full metric map + pagination |
| `get_metric_data` batching ≤500 | No | Comment only | PARTIAL | Chunk + NextToken |
| Cost estimate from pricing JSON | Partial | EC2 hourly rate inserted as cost | PARTIAL | Usage × price; all types |
| Cost Explorer actual bills | No | Not in code (V2) | NOT IMPLEMENTED (correct vs Outline) | Keep out of MVP |
| Isolation Forest inference | Partial | `inference.py` + pkl load | REAL if pkl present | Align contamination/threshold/gate |
| Z-score cold start | Partial | Only if no model file | PARTIAL | Gate on sample count |
| EWMA day 3–7 | No | No EWMA code | MISSING | Implement or delete from docs |
| Idle compute anomaly | Partial | CPU max<2% over 24h, no network | PARTIAL | Match PRD 2h / 5% / network |
| Runaway Lambda anomaly | No | Type never emitted | MISSING | Rule after IF score |
| Untagged resource anomaly | No | Scenario JSON only | MISSING | Rule-based detector |
| Unused volume / traffic spike | No | Seed delete policy only | PLACEHOLDER | Remove delete policy |
| Policy JSONB engine | Partial | AND/OR + ops; bad idle_score | PARTIAL | Fix context + priority |
| Safety: env kill-switch | Partial | Env only, startup | PARTIAL | Runtime DB flag |
| Safety: dry-run | Partial | Checked at execute, not at schedule | PARTIAL | Honor before boto3 |
| Safety: daily action cap | Partial | Counts dry_run=false | PARTIAL | Define whether proposals count |
| Safety: cooldown | Partial | Any recent action | PARTIAL | |
| Safety: protected tag/column | Yes | `safety_layer.py` | REAL | Add do-not-stop / denylist |
| Safety: budgets / CW rate limit | No | Settings unused | MISSING | Enforce or remove env vars |
| Auto-execute LOW/MEDIUM | No | No scheduler execute | MISSING | Close the loop |
| Verify post-state | No | `post_state={"result": message}` | PLACEHOLDER | Poll AWS |
| Rollback EC2 start | Partial | `start_instance` exists | REAL if called | IAM + tests |
| Rollback Lambda restore | Partial | `remove_function_concurrency` | REAL in adapter; IAM missing | Fix IAM |
| Apply tags | No | Mock success | MOCKED | Real API |
| Emergency stop API | No | Route absent | MISSING | Add + wire SafetyLayer |
| JWT on all routes except health | No | Only `/actions` | MISSING | Global dependency |
| Health payload (db/ml/aws/telemetry) | No | Stub `"pending"` | PLACEHOLDER | Real probes |
| Dashboard overview/cost-trend | Partial | `/summary` only | PARTIAL | Spec routes |
| 90-day metric retention job | No | Not in scheduler | MISSING | Cron DELETE |
| Daily IF train 02:00 UTC | No | Weekly Sunday | MISSING | Align cadence |
| TimescaleDB | No | Standard PG | Docs conflict | Fix PRD |
| SQLAlchemy | No | supabase-py + asyncpg | Docs conflict (PRD) | Fix PRD |
| structlog JSON logs | No | stdlib logging | MISSING | Implement or drop dep |
| GitHub Actions CI | No | Empty `ci.yml` | PLACEHOLDER | Fill file |
| Render deploy | No | Dockerfile COPY broken | BROKEN | Fix image |
| Docker Compose backend | No | Same Dockerfile | BROKEN | Fix image |
| Supabase RLS | Partial | Enabled; too-broad UPDATE | PARTIAL | Column checks |
| Supabase Realtime | No | Not in migrations | MISSING | Publication or drop claim |
| Terraform demo | Partial | EC2/EBS/S3 only | CONFLICTS with “no Terraform” | One demo path |
| GCP adapter | Partial | Instances stop/start; rest stub; ABC incomplete | PLACEHOLDER / CRASH if selected | Remove or finish |
| Frontend dashboard | No | Not in git (out of review scope) | MISSING | Separate workstream |
| README setup | No | Empty file | MISSING | Write README |

**Cloud operations label (summary):**  
AWS EC2 describe/stop/start: **REAL** (boto3). Lambda list/put concurrency/delete concurrency: **REAL** in adapter, **IAM incomplete**, **limit always 0**. Tags: **MOCKED** in runner. S3/RDS/EBS discovery in service: **NOT CONNECTED**. CloudWatch: **REAL API**, **EC2-only queries**. GCP: **PARTIAL / STUB**. ML IF: **REAL sklearn** with **heuristic idle overlay**. Policy/safety: **PARTIAL**. Action auto-run: **NOT EXECUTED**.

---

# Architecture Reality Check

## Documented Architecture

PRD: AWS → Telemetry Collector (APScheduler) → SQLAlchemy → PostgreSQL+TimescaleDB → ML service + FastAPI (policy, safety) → Action Runner → AWS; React dashboard.

Outline/Tech Stack: AWS → AWSAdapter → Discovery/Telemetry → Supabase PostgreSQL (`supabase-py` + `asyncpg`) → ML in-process → PolicyEngine → SafetyLayer → ActionRunner → audit; FastAPI JWT; React + Supabase Realtime. GCP/Terraform/TimescaleDB/SQLAlchemy explicitly out.

## Actual Architecture

```
[optional AWS account]
        │ boto3 (EC2 describe/stop/start, Lambda list/concurrency, CW get_metric_data)
        ▼
FastAPI process (uvicorn, 1 worker)
  ├── APScheduler (in-process)
  │     ├── Discovery (EC2+Lambda stub) → supabase-py upsert resources  ✗ needs UNIQUE
  │     ├── Telemetry (EC2 CPU/net) → asyncpg COPY resource_metrics
  │     ├── Cost stub (EC2 rate) → supabase-py insert cost_records
  │     ├── AnomalyDetector (idle rule + optional IF) → supabase-py insert anomalies
  │     ├── PolicyEngine → supabase-py insert optimization_actions (pending)
  │     └── MLRetraining (weekly) → joblib /models relative path
  ├── REST (mostly no auth)
  │     └── /actions (JWT HS256) → ActionRunner  ← only execution entry
  └── SafetyLayer ← reads env settings, not DB

Supabase PostgreSQL (schema in db/migrations; RLS on)
Render/Docker  ✗ image build currently invalid
Frontend       ✗ not in repository
GCPAdapter     ✗ optional, incomplete, crashes if selected
Terraform      ✗ parallel demo definition, unused by backend
```

Broken links: PolicyEngine ↛ ActionRunner; ActionRunner ↛ verification job; PATCH system_config ↛ SafetyLayer; health ↛ any dependency; Dockerfile ↛ requirements/ml/data paths; IAM ↛ Lambda rollback; detector ↛ runaway/untagged types; telemetry ↛ non-EC2 metrics.

## Differences

1. No TimescaleDB, no SQLAlchemy, no React, no Realtime migration, no CI.
2. GCP and Terraform exist despite being declared out of scope.
3. Action execution is request-driven, not autonomous.
4. Auth is not a platform default; it is one router.
5. Safety config is split-brain (env vs DB).
6. ML is a mix of a 24h idle heuristic and an IF model with different thresholds than documented.

## Consequences

The system cannot be operated as “autonomous cloud cost intelligence” and cannot be exposed on a network. A demo that depends on discover → metrics → anomaly → auto-stop EC2 will fail at upsert, at telemetry coverage, at policy typing, and at execution scheduling — four independent breaks on one path.

## Recommended Architecture Changes

1. Keep a **single process** FastAPI+APScheduler (fits the team) but add a DB lease so a second replica cannot double-act.
2. One control plane for safety: `system_config` + in-memory copy, JWT-protected mutation, emergency-stop.
3. AWSAdapter remains the only boto3 owner; delete or freeze GCP until MVP ships.
4. Close WF-4/5 in-process: `anomaly insert → evaluate → (dry-run log | execute) → verify`.
5. Treat IF as optional scoring; keep idle/runaway/untagged as explicit rules with tests. Do not market EWMA until it exists.
6. Drop TimescaleDB from PRD. Track one spec in git (`README` + Outline).

---

# Production Readiness

| Area | Status | Evidence | Required Action |
|---|---|---|---|
| Security | FAIL | Unauthenticated writes; CORS `*`; exception leakage | Authn all routes; allowlist CORS; generic errors |
| Authentication | FAIL | JWT empty default, HS256, `verify_aud=False`; only `/actions` | JWKS/secret required at boot; global Depends |
| Authorization | FAIL | RLS full-row UPDATE; client-supplied `user_id`; no roles | Column RLS; actor from JWT |
| Database | FAIL | No UNIQUE for upsert; no metric uniqueness; no retention | Migrations 005+; cleanup job |
| APIs | PARTIAL | Routers exist; wrong paths; stub resource detail; no execute/emergency-stop | Align to Outline §6 |
| Cloud integrations | PARTIAL | Real boto3 EC2/Lambda/CW; S3/RDS/EBS unused; GCP stub | Finish AWS MVP; hide GCP |
| ML pipeline | PARTIAL | sklearn IF + idle heuristic; no EWMA; eval ≠ prod | Implement gate; one scoring API |
| Error handling | FAIL | Empty-list on AWS errors; swallowed pool init; bare `except` in policy | Typed failures; fail jobs |
| Testing | FAIL | 3 files; empty CI; integration skips detector and auth | Expand tests; fill ci.yml |
| Observability | FAIL | Health stub; no structlog; no job metrics | Real health; JSON logs |
| CI/CD | FAIL | Empty workflow | Implement Tech Stack §16 |
| Infrastructure | FAIL | Dockerfile COPY; Render env incomplete vs `.env.example` | Fix image; document env |
| Documentation | FAIL | Empty README; `md/` gitignored; PRD≠code | Track docs; reconcile specs |
| Secrets management | PARTIAL | `.env` gitignored; example has live URL/JWT; no gitleaks | Placeholders; rotate; scan |
| Scalability | PARTIAL | Single worker required; blocking I/O; unbounded tables | Executors; retention; replica lock |
| Disaster recovery | NOT IMPLEMENTED | No backups documented; terminate-based shutdown script | Supabase PITR; stop not terminate |
| Rate limiting | NOT IMPLEMENTED | No limiter; CW cap unused | Enforce CW cap; HTTP limits |
| Dry-run default | PASS (config) | `DRY_RUN_MODE` default true; execute honors it | Keep; still fix mock tags |

---

# Recommended Implementation Order

1. **Authentication and network exposure (P0).** Global JWT, fail-closed empty secret, CORS allowlist, stop leaking `str(e)`. Nothing else is safe on `0.0.0.0` until this lands.  
   *Depends on: none. Blocks: any deploy, any action API test.*

2. **Runtime kill-switch that ActionRunner actually reads (P0).** Emergency-stop + SafetyLayer on DB/memory.  
   *Depends on: (1) so the endpoint is not public. Blocks: enabling automation.*

3. **Schema constraints (P0).** `UNIQUE` on resources; unique metrics; fix upsert.  
   *Depends on: none logically, but do before relying on discovery data. Blocks: every downstream job.*

4. **Docker/Render boot path (P0).** Correct COPY, module path, `.dockerignore`, model/pricing paths.  
   *Depends on: (1)–(3) if this image will be public. Blocks: production deploy.*

5. **Close detect → act → verify (P0/P1).** Scheduler execute, verification poll, no concurrency=0, no delete_ebs seed, real tagging, IAM aligned.  
   *Depends on: (2) and (3). This is the product.*

6. **Make discovery + telemetry match the MVP resource list, or cut the MVP list (P1).** Empty lists on AWS errors, not on success.  
   *Depends on: (3). Feeds ML and cost.*

7. **ML correctness (P1).** Cold-start gate, one threshold, dedup anomalies, don’t pivot-crash, emit documented types so policies can match.  
   *Depends on: (6) for real features. Policies already exist and currently cannot match runaway_lambda.*

8. **Policy engine context + tests (P1).** Real idle_score, priority ASC, no match-on-parse-fail. Unit tests that docs already named.  
   *Depends on: (7) for types/features.*

9. **Health, logging, retention, budget/CW caps (P1).**  
   *Depends on: pool + jobs working. Blocks: operating the service blindly.*

10. **CI + honest README/docs (P1/P2).** Fill `ci.yml`; stop gitignoring the spec or vendor a short tracked architecture doc; delete empty claims (EWMA, TimescaleDB, Terraform-not-used).  
    *Depends on: tests from (8). Last because docs should describe the system after the loop works — but README placeholders can land earlier.*

11. **Performance/maintainability (P2/P3).** Executor for boto3, pin deps, split requirements-dev, GCP removal, magic-number cleanup.  
    *After correctness; scaling one demo account is not the current blocker.*

This order is security → integrity → boot → product loop → data completeness → ML/policy alignment → operations → docs → cleanup. Enabling `GLOBAL_AUTOMATION_ENABLED` before steps 1–5 is how you stop the wrong EC2 or zero a Lambda.

---

# Trace notes (major paths)

**WF-1 Discovery:** Scheduler 15 min → `DiscoveryService.run` → `AWSAdapter.discover_instances/functions` → parse → `upsert on_conflict=provider_id` → **fails without UNIQUE**. S3/RDS/EBS never entered.

**WF-2 Telemetry:** Scheduler 5 min → load non-terminated resources → EC2-only CW queries → `copy_records_to_table` → duplicates possible → cost job hourly unrelated to “update after telemetry”.

**WF-3 ML:** Scheduler 10 min (not 5) → 24h SQL → idle rule may insert anomaly → else IF if pkl else Z-score on CPU only → **no EWMA, no data-point gate, no type classification as documented**.

**WF-4 Policy:** Separate 10 min job → join anomalies+resources → SafetyLayer env checks → insert `pending` → **stop**.

**WF-5 Execute:** Not scheduled. `POST /actions/{id}/approve` (JWT) → background `execute_action` → dry-run or boto3 stop / concurrency 0 / fake tags → `post_state` is a message string, not AWS describe. **No verify job.**

**WF-6 Rollback:** `POST /actions/{id}/rollback` → start_instance / delete_function_concurrency. IAM missing delete concurrency.

**WF-7 Emergency stop:** **Missing.**

**Deploy:** Render health path exists but handler is fake; Docker COPY is wrong.

---

# Files inspected (backend / infra / ML / tests / docs)

Tracked git files (~70) plus gitignored specs under `md/`:

- Docs: `README.md`, `md/CloudSentry_PRD.md`, `md/CloudSentry_TechStack.md`, `md/outline.md`, `md/learn_aws.md`, `md/learn_backend.md`, `md/learn_ml.md`, `md/learn_terrform.md`
- App: `backend/app/main.py`, `config.py`, `auth.py`, `scheduler.py`, `api/*`, `services/*`, `adapters/*`, `db/*`, `schemas/models.py`
- ML: `ml/*.py`, `ml/synthetic/**`
- DB: `db/migrations/001–004`
- Infra: `backend/Dockerfile`, `docker-compose.yml`, `render.yaml`, `terraform/*`, `cloud_permissions/*`, `.github/workflows/ci.yml`, `.env.example`, `.gitignore`, `pytest.ini`
- Scripts: `scripts/*`
- Tests: `tests/test_aws_adapter.py`, `test_features.py`, `test_integration.py`
- Data: `data/pricing/*.json`

Frontend directory: **not present in git**; not reviewed.

---

# Counts

| Severity | Count (finding sections above) |
|---|---|
| Critical | 7 |
| High | 14 |
| Medium | 11 |
| Low | 2 |
| Documentation/implementation mismatches (matrix rows not Yes/REAL) | 40+ |

No source files were modified except this audit document.
