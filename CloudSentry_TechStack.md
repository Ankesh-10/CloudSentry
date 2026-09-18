# CloudSentry — Tech Stack Document
**Version:** 1.0 | **Status:** Implementation-Ready | **Date:** 2026-09-19  
**Source of Truth:** CloudSentry PRD v1.0  
**Audience:** Engineering Team (1–4 Developers)

---

## Pre-Flight Self-Check

Before producing this document, verified:
- Every PRD requirement maps to a technology ✓
- Supabase has clearly defined, non-overlapping responsibilities ✓
- No Redis, Kafka, Kubernetes, or Celery introduced ✓
- Cloud credentials never reach the frontend ✓
- Supabase service-role key never reaches the frontend ✓
- ML runs in the backend process — not in Supabase ✓
- Background jobs have a concrete execution mechanism ✓
- Stack is affordable for a student project ✓
- No fabricated APIs or capabilities ✓
- Exactly one final recommended stack ✓

---

## 1. Recommended Stack — Master Table

| Layer | Technology | Purpose | Runs Where |
|---|---|---|---|
| **Frontend** | React 18 + Vite | Dashboard UI — cost charts, anomaly center, audit log, resource explorer | Browser (Vercel or local dev) |
| **UI Components** | shadcn/ui + Tailwind CSS | Consistent, low-effort component system | Browser |
| **Charting** | Recharts | Time-series cost/metric charts, sparklines, before/after overlays | Browser |
| **API Client** | Axios + TanStack Query | HTTP requests to FastAPI backend; caching, stale-state, refetch intervals | Browser |
| **Realtime Client** | `@supabase/supabase-js` | Subscribe to anomaly/action events via Supabase Realtime | Browser |
| **Auth (Frontend)** | Supabase Auth (`@supabase/supabase-js`) | Session management, token storage, login UI | Browser |
| **Backend API** | Python 3.11 + FastAPI | REST API, policy engine, safety layer, action runner | Docker container (local / Render) |
| **Validation** | Pydantic v2 | Request/response schema validation | Backend process |
| **DB Access** | `supabase-py` (PostgREST client) | CRUD on relational tables (resources, anomalies, actions, audit) | Backend process |
| **Time-Series Queries** | `asyncpg` + raw SQL | Direct PostgreSQL queries for metric hypertable (time-bucket aggregations) | Backend process |
| **Authentication** | Supabase Auth | Single-user admin login; JWT verification in backend | Supabase cloud + backend |
| **Database** | Supabase PostgreSQL | All relational data + `resource_metrics` time-series table | Supabase cloud |
| **Realtime** | Supabase Realtime | Push anomaly/action state changes to dashboard | Supabase cloud → browser |
| **Row Level Security** | Supabase RLS | Table-level access control per role | Supabase cloud |
| **ML** | scikit-learn + pandas + NumPy | Isolation Forest training + inference; Z-score fallback | Backend process (same container) |
| **Model Serialization** | joblib | Save/load trained `.pkl` models | Backend filesystem (bind-mounted volume) |
| **Cloud SDK** | boto3 | All AWS API calls: discovery, CloudWatch, optimization actions | Backend process |
| **Background Jobs** | APScheduler (in-process) | Periodic: discovery (15 min), telemetry (5 min), ML training (daily), verification | Backend process |
| **Config** | python-dotenv + Pydantic Settings | Environment variable loading and validation | Backend process |
| **Logging** | Python `structlog` | Structured JSON logs; component, resource_id, latency fields | Backend process → stdout |
| **Testing (Backend)** | pytest + pytest-asyncio | Unit, integration, safety, ML tests | Local / CI |
| **AWS Mocking** | moto | Mock boto3 in unit/integration tests — no real AWS calls | Local / CI |
| **Testing (Frontend)** | Vitest + React Testing Library | Component tests for dashboard views | Local / CI |
| **Containerization** | Docker + Docker Compose | Single-command local dev; bundle backend + local DB tooling | Local dev |
| **Database Migrations** | Supabase Migrations (SQL files) | Schema versioning via Supabase CLI | Local + Supabase cloud |
| **Deployment — Frontend** | Vercel | Static React app; free tier sufficient | Vercel cloud |
| **Deployment — Backend** | Render (free/starter tier) | Python/Docker service; persistent worker | Render cloud |
| **CI/CD** | GitHub Actions | Lint → test → build → deploy on push to main | GitHub |
| **Secrets (CI)** | GitHub Actions Secrets | AWS credentials, Supabase keys for CI pipeline | GitHub |

**NOT IN STACK:** TimescaleDB extension, Redis, Celery, Kafka, Kubernetes, Prophet, GraphQL, InfluxDB, MongoDB, LangChain, vector database, dedicated APM platform.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        AWS Account                           │
│   EC2 │ Lambda │ S3 │ RDS │ EBS │ CloudWatch                 │
└───────────────────────┬──────────────────────────────────────┘
                        │  boto3
                        ▼
┌──────────────────────────────────────────────────────────────┐
│              FastAPI Backend (Python 3.11)                    │
│                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────┐  │
│  │ APScheduler     │  │  REST API       │  │ ML Pipeline │  │
│  │ - Discovery     │  │  - /resources   │  │ - IF Model  │  │
│  │ - Telemetry     │  │  - /anomalies   │  │ - Z-score   │  │
│  │ - ML Training   │  │  - /actions     │  │   fallback  │  │
│  │ - Verification  │  │  - /dashboard   │  └──────┬──────┘  │
│  └────────┬────────┘  │  - /system      │         │         │
│           │           └────────┬────────┘         │         │
│           │                    │                   │         │
│           └──────────┬─────────┘                   │         │
│                      │  Policy Engine               │         │
│                      │  Safety Layer ◄──────────────┘         │
│                      │  Action Runner                         │
└──────────────────────┼────────────────────────────────────────┘
                       │
              ┌────────┴────────┐
              │                 │
              │  supabase-py    │  asyncpg (raw SQL for
              │  (PostgREST)    │  time-series queries)
              │                 │
              └────────┬────────┘
                       │
┌──────────────────────▼────────────────────────────────────────┐
│                     Supabase                                   │
│                                                                │
│  PostgreSQL                                                    │
│  ├── cloud_accounts    (relational)                            │
│  ├── resources         (relational)                            │
│  ├── resource_metrics  (append-only, indexed by time)         │
│  ├── cost_records      (relational)                            │
│  ├── anomalies         (relational + JSONB)                    │
│  ├── optimization_actions (relational + JSONB)                 │
│  ├── audit_logs        (append-only)                           │
│  ├── policies          (relational + JSONB conditions)         │
│  └── system_config     (key-value)                             │
│                                                                │
│  Auth          → session tokens, user management               │
│  Realtime      → anomalies, optimization_actions inserts       │
│  RLS           → table-level access control                    │
└───────────────────────┬────────────────────────────────────────┘
                        │ Supabase Realtime (WebSocket)
                        │ REST API (anon key, JWT-restricted)
                        ▼
┌──────────────────────────────────────────────────────────────┐
│              React 18 Frontend (Vite)                         │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │  Supabase    │  │  Axios +     │  │  Recharts         │  │
│  │  Auth Client │  │  TanStack    │  │  Cost trends      │  │
│  │  Realtime    │  │  Query       │  │  Metric charts    │  │
│  │  subscriber  │  │  (FastAPI)   │  │  Sparklines       │  │
│  └──────────────┘  └──────────────┘  └───────────────────┘  │
│                                                              │
│  Views: Overview │ AnomalyCenter │ OptimizationCenter        │
│         ResourceExplorer │ AuditLog │ CostAnalytics           │
└──────────────────────────────────────────────────────────────┘
```

### Component Locations

| Component | Runs Where | Communication |
|---|---|---|
| React Frontend | Vercel (prod) / localhost:5173 (dev) | HTTPS REST to FastAPI; WebSocket to Supabase Realtime |
| FastAPI Backend | Render (prod) / localhost:8000 (dev) | asyncpg + supabase-py to Supabase; boto3 to AWS |
| ML (inline) | Same process as FastAPI backend | In-memory; writes anomaly records to Supabase via supabase-py |
| APScheduler | Same process as FastAPI backend | In-process; calls service functions directly |
| Supabase | Supabase cloud (always) | PostgREST API from backend; Realtime WS to frontend |
| AWS APIs | AWS cloud | boto3 from backend only |

---

## 3. Supabase Architecture

### 3.1 PostgreSQL

**Decision: Standard PostgreSQL without TimescaleDB extension.**

Supabase runs managed PostgreSQL. TimescaleDB is NOT available as an extension on Supabase's managed platform. [VERIFY: Supabase extensions list at supabase.com/docs/guides/database/extensions — confirm `timescaledb` availability. As of latest documentation, TimescaleDB is NOT listed as a supported extension on Supabase managed instances.]

**Time-series storage without TimescaleDB:**  
The `resource_metrics` table uses standard PostgreSQL with the following optimizations:

```sql
CREATE TABLE resource_metrics (
    id          BIGSERIAL,
    time        TIMESTAMPTZ NOT NULL,
    resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    metric_name VARCHAR(50) NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    unit        VARCHAR(20),
    PRIMARY KEY (id)
);

-- Covering index for the primary query pattern: fetch metrics for a resource over a time range
CREATE INDEX idx_metrics_resource_time
    ON resource_metrics (resource_id, time DESC);

-- Partial index for recent data (last 7 days — most-queried window)
CREATE INDEX idx_metrics_recent
    ON resource_metrics (resource_id, metric_name, time DESC)
    WHERE time > NOW() - INTERVAL '7 days';
```

**Justification:** At MVP scale (≤ 5 resources, 10 metrics, 5-min interval), this generates ≤ 10 × 5 × 288 = ~14,400 rows/day. Standard PostgreSQL with proper indexes handles this trivially. No hypertable required.

**Retention:** Rows older than 90 days deleted by a daily APScheduler job: `DELETE FROM resource_metrics WHERE time < NOW() - INTERVAL '90 days'`. This runs in the backend, not in Supabase.

**Time-bucket aggregation (no TimescaleDB):**
```sql
-- Equivalent of time_bucket('1 hour', time) using standard SQL:
SELECT
    date_trunc('hour', time) AS bucket,
    resource_id,
    metric_name,
    AVG(value) AS avg_value
FROM resource_metrics
WHERE resource_id = $1
  AND metric_name = $2
  AND time >= NOW() - INTERVAL '24 hours'
GROUP BY bucket, resource_id, metric_name
ORDER BY bucket DESC;
```

**Supabase Free Tier Storage:** [VERIFY: Supabase free tier includes 500MB database storage. At ~14,400 rows/day × ~100 bytes/row ≈ 1.4MB/day, 90-day retention ≈ 126MB. Within free tier.]

**All Tables:**

| Table | Type | Notes |
|---|---|---|
| `cloud_accounts` | Relational | Single row for MVP (one AWS account) |
| `resources` | Relational | Updated on each discovery cycle |
| `resource_metrics` | Append-only, time-indexed | Primary time-series table |
| `cost_records` | Relational | Estimated + actual cost per resource per period |
| `anomalies` | Relational + JSONB | `features_snapshot` in JSONB |
| `optimization_actions` | Relational + JSONB | `pre_state`, `post_state` in JSONB |
| `audit_logs` | Append-only | Never updated after insert |
| `policies` | Relational + JSONB | `conditions` tree in JSONB |
| `system_config` | Key-value | Kill-switch, limits, dry-run flag |

**Migrations:** Managed via Supabase CLI (`supabase migration new`, `supabase db push`). All schema files committed to `db/migrations/`. No Alembic needed — Supabase CLI handles this.

---

### 3.2 Authentication

**Used for:** Dashboard login. Single admin user for MVP.

**Implementation:**
- Supabase Auth Email/Password provider
- Admin account created manually in Supabase dashboard or via `supabase auth admin createuser` CLI
- Frontend: `supabase.auth.signInWithPassword({email, password})` → receives JWT access token
- Backend: Validates JWT on every request using Supabase's public JWKS endpoint  
  [VERIFY: `https://<project>.supabase.co/auth/v1/.well-known/jwks.json` — confirm endpoint in Supabase Auth docs]
- FastAPI dependency: extract `Authorization: Bearer <token>` → validate with `python-jose` or `PyJWT` against Supabase JWKS

**Roles defined in Supabase:**
| Role | Description |
|---|---|
| `anon` | Unauthenticated — no table access (RLS blocks all) |
| `authenticated` | Logged-in dashboard user — read access to all tables, write access to `system_config` |
| `service_role` | Backend service — full access, bypasses RLS; key stored backend-only |

**Session flow:**
1. User submits email/password in React login page
2. `@supabase/supabase-js` calls Supabase Auth → returns `access_token` (JWT)
3. Token stored in memory / `localStorage` (acceptable for demo)
4. All FastAPI requests include `Authorization: Bearer <access_token>`
5. FastAPI verifies token signature and `role` claim

---

### 3.3 Row Level Security (RLS)

RLS is enabled on all tables. Backend uses `service_role` key (bypasses RLS). Frontend uses `anon` key + authenticated JWT (subject to RLS).

**CRITICAL:** The frontend MUST only use the `anon` key. The `service_role` key MUST only exist in backend environment variables.

| Table | RLS Policy | Rationale |
|---|---|---|
| `cloud_accounts` | `authenticated` can SELECT; no INSERT/UPDATE from frontend | Cloud account config is backend-managed |
| `resources` | `authenticated` can SELECT; no INSERT/UPDATE from frontend | Populated by backend collector |
| `resource_metrics` | `authenticated` can SELECT; no INSERT from frontend | Written by backend collector |
| `cost_records` | `authenticated` can SELECT | Written by backend |
| `anomalies` | `authenticated` can SELECT and UPDATE `status` field only | Allow false_positive marking from dashboard |
| `optimization_actions` | `authenticated` can SELECT; can UPDATE `status` to `approved` only | Allow approval flow from dashboard |
| `audit_logs` | `authenticated` can SELECT only | Append-only; no frontend writes |
| `policies` | `authenticated` can SELECT and UPDATE | Policy management from dashboard |
| `system_config` | `authenticated` can SELECT and UPDATE | Kill-switch toggle from dashboard |

**Frontend NEVER writes to:** `resource_metrics`, `cloud_accounts`, `resources`, `cost_records`, `audit_logs`.  
**Backend (service_role) bypasses RLS** for all operations.

---

### 3.4 Realtime

Supabase Realtime is used for two specific events where the dashboard must update without polling:

| Event | Table | Change Type | Frontend Action |
|---|---|---|---|
| New anomaly detected | `anomalies` | INSERT | Show anomaly alert badge; refresh Anomaly Center |
| Optimization action status changed | `optimization_actions` | UPDATE | Update action card status (executing → completed/failed) |

**Why Realtime here:** These are the two events the user watches in real-time during a demo. Without Realtime, the user would need to manually refresh to see an anomaly appear or an action complete. Polling with TanStack Query (5-second refetch) is the fallback.

**Implementation:**
```javascript
// In AnomalyCenter component
const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)

useEffect(() => {
  const channel = supabase
    .channel('anomalies-inserts')
    .on('postgres_changes',
      { event: 'INSERT', schema: 'public', table: 'anomalies' },
      (payload) => {
        queryClient.invalidateQueries(['anomalies'])
        showToast(`New anomaly: ${payload.new.anomaly_type}`)
      }
    )
    .subscribe()
  return () => supabase.removeChannel(channel)
}, [])
```

**NOT used for:** resource metrics (too high frequency — 5-min polling via TanStack Query is sufficient), audit logs (polling acceptable), dashboard overview cards (polling acceptable).

[VERIFY: Supabase Realtime free tier — concurrent connections and messages/month limit. Confirm at supabase.com/pricing before enabling in production.]

---

### 3.5 Edge Functions

**Decision: Not required for MVP.**

No use case justifies Edge Functions in this architecture:
- ML inference runs in the Python backend (not in Deno/JavaScript Edge Functions)
- Cloud API calls require boto3 (Python) — not available in Edge Functions
- Background jobs run via APScheduler in the backend process
- Auth webhooks are not needed for single-user MVP

If in V2 we add webhook-based triggers from AWS EventBridge, Edge Functions could receive events. Not applicable for MVP.

---

### 3.6 Storage

**Decision: Not required for MVP.**

Model files (`.pkl`) are stored on the backend server filesystem (bind-mounted Docker volume). No binary blob storage is needed in Supabase. If the backend is deployed on Render, the model volume persists across deploys.

If Render's ephemeral storage becomes an issue, model files can be stored in an S3 bucket (already in scope for the demo AWS account) and fetched on startup. Supabase Storage adds no value here.

---

## 4. Database Technology Decision

**Selected:** Supabase PostgreSQL (standard, no extensions)

| Option | Why Not |
|---|---|
| **Raw PostgreSQL (self-hosted)** | Would require managing a separate Docker container / server. Supabase provides managed PostgreSQL + Auth + Realtime as a bundle. For a student project, the operational simplicity of Supabase outweighs the minor control difference. |
| **TimescaleDB** | Not available on Supabase managed instances [VERIFY]. Self-hosting TimescaleDB would eliminate Supabase's managed benefits. At MVP scale (< 15K rows/day), standard PostgreSQL with indexes is sufficient. |
| **InfluxDB** | Separate system entirely. Would require maintaining two databases — one for relational data, one for metrics. Doubles operational complexity. No benefit at this scale. |
| **MongoDB** | JSONB in PostgreSQL covers the document storage needs (policy conditions, feature snapshots). No reason to introduce MongoDB. |
| **Firebase Firestore** | No SQL, no joins, no time-series queries. Would make anomaly detection feature queries painful. |
| **Redis** | No caching requirement exists. API responses are fast enough from Supabase PostgreSQL at MVP scale. No pub/sub requirement (Supabase Realtime covers it). |

**Conclusion:** Supabase PostgreSQL satisfies all requirements: relational data, time-series metrics (with standard indexes), JSONB for structured config/snapshots, Auth integration, and Realtime — all in one managed service. No additional database is introduced.

---

## 5. Backend Stack

### Framework and Runtime

```
Python 3.11
└── FastAPI 0.111+
    ├── Pydantic v2          — request/response validation, settings management
    ├── uvicorn              — ASGI server
    ├── python-jose[cryptography] — JWT verification (Supabase tokens)
    └── python-dotenv        — .env loading
```

### Database Access Strategy

Two clients, for two different query patterns:

| Client | Purpose | Why |
|---|---|---|
| `supabase-py` (PostgREST) | CRUD on relational tables: resources, anomalies, actions, audit_logs, policies, system_config | Simple, type-safe, auth-aware; no SQL needed for standard inserts/selects |
| `asyncpg` (raw async PostgreSQL) | Time-series queries on `resource_metrics` (aggregations, window functions, bulk inserts) | PostgREST cannot efficiently express `date_trunc` + `GROUP BY` + `WHERE time > ...` queries; raw SQL is required for ML feature extraction |

**Do NOT use SQLAlchemy ORM.** With Supabase, `supabase-py` replaces the ORM for relational tables. SQLAlchemy adds a third DB abstraction layer with no benefit. Use `asyncpg` for raw SQL where needed.

**Connection string for asyncpg:** Supabase provides a direct PostgreSQL connection string (Transaction Pooler for serverless, Session Pooler for persistent connections). [VERIFY: Supabase connection pooling options in project settings — use Session Pooler for Render deployment.]

### Cloud Integration

```
boto3 (latest stable)
└── AWSAdapter class
    ├── ec2 client       — discovery, stop/start, tags, volumes
    ├── lambda_ client   — list functions, concurrency, tags
    ├── s3 client        — list buckets, tags
    ├── rds client       — describe DB instances, stop/start
    └── cloudwatch client — get_metric_data (batched)
```

All boto3 calls wrapped in `AWSAdapter`. No direct boto3 calls in business logic.

### Background Jobs

```
APScheduler 3.x (AsyncIOScheduler)
├── resource_discovery       — interval: 15 min
├── telemetry_collection     — interval: 5 min
├── cost_record_update       — interval: 1 hour
├── ml_inference             — interval: 5 min (post-collection)
├── ml_model_training        — cron: daily at 02:00 UTC
├── action_verification      — interval: 2 min (checks pending verifications)
└── metric_retention_cleanup — cron: daily at 03:00 UTC
```

APScheduler runs in the same process as FastAPI. Jobs are registered at startup in `lifespan` context manager. No Celery, no Redis, no external worker process.

### Error Handling

- All boto3 calls wrapped in try/except for `ClientError`, `BotoCoreError`
- All Supabase calls check response for errors (supabase-py raises on HTTP errors)
- Failed jobs logged via structlog; job continues next interval
- No silent failures: every exception produces a structured log entry

---

## 6. Cloud Integration Stack

**Selected Provider: AWS**

| Service | SDK/API | Purpose | Required Permissions | Data Collected | Actions Possible | Limitations |
|---|---|---|---|---|---|---|
| **EC2** | `boto3.client('ec2')` | Discover instances, state tracking | `ec2:Describe*`, `ec2:StopInstances`, `ec2:StartInstances`, `ec2:CreateTags`, `ec2:DeleteTags` | ID, type, state, region, tags, launch_time | Stop, Start, Tag | Stop does not terminate; no TerminateInstances permission |
| **CloudWatch (EC2)** | `boto3.client('cloudwatch')` | CPU, network metrics | `cloudwatch:GetMetricData`, `cloudwatch:ListMetrics` | CPUUtilization, NetworkIn, NetworkOut | None (read-only) | Memory not available without CloudWatch Agent |
| **Lambda** | `boto3.client('lambda')` | Discover functions, limit concurrency | `lambda:ListFunctions`, `lambda:GetFunction`, `lambda:PutFunctionConcurrency`, `lambda:DeleteFunctionConcurrency`, `lambda:TagResource` | Name, runtime, memory, timeout, last_modified | Set/delete reserved concurrency, Tag | Concurrency=0 throttles all invocations |
| **CloudWatch (Lambda)** | `boto3.client('cloudwatch')` | Invocations, duration, errors | `cloudwatch:GetMetricData` | Invocations, Duration (avg), Errors | None | 1-min minimum granularity (standard monitoring) |
| **S3** | `boto3.client('s3')` | Discover buckets | `s3:ListAllMyBuckets`, `s3:GetBucketLocation`, `s3:GetBucketTagging`, `s3:PutBucketTagging` | Bucket name, region, creation_date | Tag buckets | Bucket-level metrics only in CloudWatch (daily for size) |
| **CloudWatch (S3)** | `boto3.client('cloudwatch')` | Storage size, object count | `cloudwatch:GetMetricData` | BucketSizeBytes, NumberOfObjects | None | Updated once per day, not real-time |
| **RDS** | `boto3.client('rds')` | Discover DB instances | `rds:DescribeDBInstances`, `rds:StopDBInstance`, `rds:StartDBInstance`, `rds:AddTagsToResource` | ID, class, engine, status, multi_az | Stop, Start (requires approval — HIGH risk) | Stop requires instance to be in `available` state; stopped RDS auto-starts after 7 days [VERIFY: confirm current behavior in AWS docs] |
| **CloudWatch (RDS)** | `boto3.client('cloudwatch')` | DB connections, free storage | `cloudwatch:GetMetricData` | DatabaseConnections, FreeStorageSpace | None | — |
| **EBS** | `boto3.client('ec2')` | Discover volumes, track attachment | `ec2:DescribeVolumes` | ID, size, state, attachment, type | Tag (via ec2:CreateTags) | No delete without explicit approval — HIGH risk |
| **CloudWatch (EBS)** | `boto3.client('cloudwatch')` | Read/write ops | `cloudwatch:GetMetricData` | VolumeReadOps, VolumeWriteOps | None | No metrics emitted when volume is unattached |
| **Cost Explorer** | `boto3.client('ce')` | Actual billing data (V2 only) | `ce:GetCostAndUsage` | Per-service daily cost | None | 24–48h lag; not used for real-time detection in MVP |

**Batching:** Use `cloudwatch.get_metric_data()` with multiple `MetricDataQuery` entries per call (up to 500 [VERIFY]) rather than `get_metric_statistics()` per metric. Reduces API calls significantly.

**Retry strategy:** All boto3 calls use `botocore.config.Config(retries={'max_attempts': 3, 'mode': 'adaptive'})`.

---

## 7. ML Stack

### Selected Stack

```
Python 3.11
├── scikit-learn   — IsolationForest
├── pandas         — feature engineering, rolling windows
├── NumPy          — numerical operations
├── scipy          — Z-score calculation (scipy.stats.zscore)
└── joblib         — model serialization (.pkl files)
```

**Prophet: NOT USED.** Requires weeks of daily data with seasonality. MVP cannot guarantee this data volume at demo time. Z-score + Isolation Forest covers the MVP anomaly types.

**SHAP: OPTIONAL (V2).** If time permits, `shap` library can generate feature contribution explanations for IF output. Not in base dependencies.

### ML Pipeline (Concrete)

```
1. DATA FETCH
   asyncpg → SELECT from resource_metrics
   Window: last 30 days (training), last 12 points / 1 hour (inference)

2. FEATURE ENGINEERING (pandas)
   Per resource, per collection cycle:
   - rolling_avg_cpu_1h      = rolling mean (12 points) of CPUUtilization
   - rolling_avg_cpu_24h     = rolling mean (288 points) of CPUUtilization
   - cpu_deviation_ratio     = current / rolling_avg_cpu_24h  (0 if avg = 0)
   - network_in_ratio        = current NetworkIn / 24h avg NetworkIn
   - invocation_spike_ratio  = current Invocations / 24h avg Invocations
   - error_rate              = Errors / Invocations (Lambda only)
   - hours_unattached        = hours since volume state = 'available' (EBS only)
   - time_of_day             = hour(0–23) of collection timestamp

3. COLD START GATE
   IF data_points_available < 144 (= 12h):  # too sparse for IF
     → USE Z-SCORE FALLBACK
       z = (current_value - rolling_mean) / rolling_std
       anomaly if |z| > 2.5 AND direction matches anomaly type
     → WRITE anomaly with model_version = 'zscore_fallback'
   ELSE IF data_points_available < 2016 (= 7 days):
     → USE EWMA THRESHOLD
   ELSE:
     → USE ISOLATION FOREST

4. ISOLATION FOREST TRAINING (daily APScheduler job)
   X = feature matrix for last 30 days
   model = IsolationForest(n_estimators=100, contamination=0.05,
                           random_state=42, n_jobs=-1)
   model.fit(X)
   joblib.dump(model, f'/models/if_{resource_type}_{today}.pkl')
   # Keep last 5 versions; delete older

5. INFERENCE (every 5-min collection cycle)
   model = joblib.load(latest_model_path)
   score = model.score_samples([feature_vector])[0]
   # score in [-1, 0]: more negative = more anomalous
   is_anomaly = score < -0.3   # tunable threshold
   confidence = (abs(score) - 0.3) / 0.7   # normalized to [0, 1] when score < -0.3
   severity = 'HIGH' if score < -0.7 else 'MEDIUM' if score < -0.5 else 'LOW'

6. ANOMALY RECORD
   supabase.table('anomalies').insert({
     resource_id, anomaly_type, severity, anomaly_score=score,
     confidence, reason=generated_string, features_snapshot=feature_dict,
     model_version=model_filename
   })

7. EVALUATION (synthetic test set, offline)
   Target: Precision ≥ 0.75, Recall ≥ 0.70, F1 ≥ 0.72
   Actual: TBD after test run
```

**Where ML runs:** In the FastAPI backend process. No separate ML service. The inference cycle is triggered by APScheduler immediately after telemetry collection completes. At MVP scale, IF inference on 5 resources takes < 100ms.

---

## 8. Background Jobs / Scheduling

**Selected: APScheduler 3.x (AsyncIOScheduler)**

**Evaluation:**

| Option | Decision | Reason |
|---|---|---|
| **APScheduler** | ✅ SELECTED | In-process; no extra infra; fits FastAPI lifespan; no Redis needed |
| FastAPI `BackgroundTasks` | ✗ | Request-scoped only; not periodic |
| Cron (system) | ✗ | Requires separate process management; no visibility in app |
| Celery | ✗ | Requires Redis broker; 2× infrastructure for no gain at this scale |
| GitHub Actions | ✗ | 5-min GitHub Actions minimum interval; not suitable for 5-min polling |
| Supabase pg_cron | ✗ | Runs SQL only; cannot execute boto3 or ML code |

**Job Registration:**
```python
# backend/app/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# On FastAPI startup:
scheduler.add_job(discovery_service.run, 'interval', minutes=15)
scheduler.add_job(telemetry_service.run, 'interval', minutes=5)
scheduler.add_job(ml_service.run_inference, 'interval', minutes=5,
                  misfire_grace_time=60)
scheduler.add_job(ml_service.train_model, 'cron', hour=2, minute=0)
scheduler.add_job(action_runner.verify_pending, 'interval', minutes=2)
scheduler.add_job(cleanup_service.delete_old_metrics, 'cron', hour=3)
scheduler.start()
```

**Failure behavior:** APScheduler logs missed jobs. `misfire_grace_time=60` allows a job to run up to 60 seconds late. Jobs are independent — one failure does not block others.

---

## 9. Frontend Stack

```
React 18
├── Build tool:     Vite 5
├── Language:       TypeScript (RECOMMENDED) or JavaScript — [DESIGN DECISION: TypeScript reduces runtime errors; worth the setup]
├── Styling:        Tailwind CSS + shadcn/ui
├── Charting:       Recharts
├── API client:     Axios + TanStack Query (React Query v5)
├── Supabase:       @supabase/supabase-js (Auth + Realtime)
├── Routing:        React Router v6
└── State:          React Context (for auth/system config only) + TanStack Query cache
```

**State Management Decision:** No Redux, no Zustand, no MobX. TanStack Query manages all server state (resources, anomalies, actions, metrics). React Context manages auth state (user session) and global system config (dry-run flag, automation status). This is sufficient.

**Dashboard Component Requirements:**

| View | Charting Need | Library |
|---|---|---|
| Cost Analytics | Line chart (cost over time), bar chart (per-resource) | Recharts `<LineChart>`, `<BarChart>` |
| Metric sparklines | Mini line charts in table rows | Recharts `<Sparkline>` or custom SVG |
| Before/after comparison | Two sparklines side by side | Recharts |
| Anomaly score | Number badge + colored severity indicator | shadcn/ui Badge |
| Resource utilization | Progress bar | shadcn/ui Progress |
| Audit log | Virtualized scrollable table | shadcn/ui Table (small dataset — no virtualization needed for MVP) |

**API communication from frontend:**
- All state-modifying operations (approve action, rollback, emergency stop, mark false_positive) → POST/PATCH to FastAPI backend (requires JWT)
- All read operations for time-series metrics → GET FastAPI backend (which queries asyncpg)
- Realtime anomaly/action updates → Supabase Realtime WebSocket (`@supabase/supabase-js`)
- Simple table reads (resources list, anomaly list, audit log) → can query Supabase PostgREST directly with `anon` key + JWT (RLS-protected)

**[DESIGN DECISION]** For reads, the frontend can query Supabase PostgREST directly (bypassing FastAPI) for tables where RLS is sufficient. This simplifies FastAPI and removes proxying overhead for read-only data. For writes and cloud operations, all traffic goes through FastAPI.

---

## 10. API Communication

```
Frontend  ──[HTTPS REST + JWT]──►  FastAPI Backend
Frontend  ──[HTTPS REST + JWT]──►  Supabase PostgREST (read-only, RLS)
Frontend  ◄──[WebSocket]─────────  Supabase Realtime

FastAPI   ──[asyncpg TCP]────────►  Supabase PostgreSQL (direct connection)
FastAPI   ──[supabase-py HTTPS]──►  Supabase PostgREST (writes to relational tables)
FastAPI   ──[boto3 HTTPS]────────►  AWS APIs (CloudWatch, EC2, Lambda, S3, RDS)
```

**Authentication flow:**
1. Frontend logs in via `supabase.auth.signInWithPassword()` → receives Supabase JWT
2. Frontend sends JWT in `Authorization: Bearer <token>` header to FastAPI
3. FastAPI verifies JWT signature against Supabase JWKS
4. FastAPI uses its own `SUPABASE_SERVICE_ROLE_KEY` (never seen by frontend) for DB writes

**No GraphQL.** REST is sufficient for all endpoints defined in PRD Section 11. GraphQL would add complexity with no benefit at this API surface size.

**No WebSocket from frontend to FastAPI.** Realtime updates come from Supabase Realtime only. FastAPI serves synchronous REST responses.

---

## 11. Security Stack

### Credential Separation

| Credential | Where Stored | Who Can Access | Risk if Leaked |
|---|---|---|---|
| `SUPABASE_URL` | Frontend `.env` (VITE_SUPABASE_URL) | Public — safe | None (URL is not a secret) |
| `SUPABASE_ANON_KEY` | Frontend `.env` (VITE_SUPABASE_ANON_KEY) | Public — safe (RLS protects data) | Low — RLS prevents unauthorized data access |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend `.env` only | Backend process only | CRITICAL — full DB bypass |
| `AWS_ACCESS_KEY_ID` | Backend `.env` only | Backend process only | CRITICAL — cloud operations |
| `AWS_SECRET_ACCESS_KEY` | Backend `.env` only | Backend process only | CRITICAL — cloud operations |
| `JWT_SECRET` | Not needed — Supabase manages JWT | — | — |

**Rules:**
1. `SUPABASE_SERVICE_ROLE_KEY` NEVER in frontend code, never in browser DevTools, never in Docker logs
2. `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` NEVER in frontend, never in any committed file
3. `.env` in `.gitignore` — enforce in CI with `git-secrets` or pre-commit hook
4. Production (Render): inject secrets as environment variables — never in Dockerfile or image

### IAM (AWS)

Dedicated IAM user `cloudsentry-agent`. Policy: least privilege as defined in PRD Section 16.

**NEVER grant:** `ec2:TerminateInstances`, `s3:DeleteBucket`, `rds:DeleteDBInstance`, `iam:*`, `sts:AssumeRole` (unless needed for role-based auth).

### Backend Auth

FastAPI dependency `get_current_user`:
```python
from jose import jwt, JWTError

async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"])
        # or verify against JWKS endpoint for RS256
        return payload
    except JWTError:
        raise HTTPException(status_code=401)
```

[VERIFY: Supabase JWT algorithm (HS256 with project JWT secret vs RS256 with JWKS). Check project API settings in Supabase dashboard.]

### HTTPS

- Local dev: HTTP acceptable (localhost)
- Render deployment: HTTPS provided automatically
- Vercel: HTTPS provided automatically

---

## 12. Observability Stack

**Lightweight approach — no dedicated APM platform.**

| Concern | Tool | Implementation |
|---|---|---|
| Application logs | `structlog` | JSON to stdout; Render/Vercel capture logs |
| Health check | FastAPI `/health` endpoint | Returns DB, ML, AWS connectivity, telemetry staleness |
| Error tracking | `structlog` + log aggregation | Render logs viewer for prod; local terminal for dev |
| Job execution | APScheduler listeners | Log job start, end, duration, exception |
| ML model status | Logged on load/train | model_version, training_duration, row_count |
| AWS API errors | boto3 exception logging | ClientError → structured log with service, operation, error_code |
| Telemetry staleness | Computed in health endpoint | `(NOW() - last_collection_time).seconds > 600` → stale |
| Action outcomes | `audit_logs` table in Supabase | Every action: actor, api_call, pre_state, post_state, result |

**Distinction:**
- **System health** = CloudSentry's own DB connectivity, job status, ML model status, API latency
- **Cloud resource anomalies** = AWS account cost and usage anomalies being monitored
- **ML health** = model mode (IF vs Z-score), last training time, inference duration

These MUST NOT be mixed in the same log lines or dashboard views.

---

## 13. Testing Stack

### Backend (pytest)

```
pytest
├── pytest-asyncio       — async FastAPI endpoint testing
├── httpx                — FastAPI TestClient async
├── moto[all]            — Mock entire boto3 AWS API surface
├── pytest-mock          — General mocking utilities
└── factory-boy          — Test fixture factories for Supabase data
```

**Test Categories:**
- `tests/unit/` — ML functions, policy evaluator, safety layer, cost estimator (no I/O)
- `tests/integration/` — FastAPI endpoints + Supabase (use Supabase local dev stack or test project)
- `tests/cloud/` — Real AWS tests (marked `@pytest.mark.cloud` — skipped in CI unless explicit)
- `tests/safety/` — Kill-switch, action limits, dry-run enforcement
- `tests/ml/` — IF training on synthetic data, Z-score detection, cold-start fallback

**Supabase test strategy:** Use a separate Supabase project for testing OR use `supabase start` (local Docker-based Supabase) for integration tests. [VERIFY: `supabase start` spins up local Supabase stack — confirm in Supabase CLI docs.]

### Frontend (Vitest)

```
vitest
├── @testing-library/react   — Component rendering tests
├── @testing-library/user-event — User interaction simulation
└── msw (Mock Service Worker) — Mock FastAPI and Supabase responses
```

**Test scope:** Anomaly card rendering, approval button behavior, emergency stop UI state, stale data warning display. Not pixel-perfect UI testing.

### ML Testing

```python
# Synthetic anomaly test
def test_isolation_forest_detects_idle():
    normal_data = generate_normal_cpu_series(n=1000, mean=60, std=10)
    anomaly_vector = np.array([[0.5, 0, 0, 0, 0, 0, 0, 0, 0]])  # cpu=0.5%
    
    model = IsolationForest(n_estimators=100, contamination=0.05, random_state=42)
    model.fit(normal_data)
    
    score = model.score_samples(anomaly_vector)[0]
    assert score < -0.3, f"Expected anomaly score < -0.3, got {score}"
```

### End-to-End

Manual demo walkthrough (scripted): `scripts/inject_anomaly.py` → verify anomaly in Supabase → verify dashboard update via Realtime → verify action created → approve → verify AWS state change.

No Playwright/Cypress for MVP — out of scope for team size.

---

## 14. Development Environment

### Runtime Versions

```
Python:   3.11.x (pin exact minor version in .python-version)
Node.js:  20.x LTS
pnpm:     9.x (preferred over npm for monorepo workspace support)
```

### Local Setup

```bash
# Backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

# Frontend
cd frontend && pnpm install

# Supabase local (optional — requires Docker)
supabase start              # Starts local Supabase stack
supabase db push            # Apply migrations to local DB

# Or: Use hosted Supabase dev project (simpler for student team)
```

### Docker Usage

Docker Compose is used only for local development convenience — NOT for the database (Supabase is managed). The `docker-compose.yml` runs:
- `backend` service (FastAPI + APScheduler)
- Optional: local Supabase via `supabase start` (alternative to hosted)

No Docker for production — backend deploys as a native Python service on Render.

### .env.example

```bash
# ── SUPABASE ──────────────────────────────────────────────
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJ...         # Frontend-safe
SUPABASE_SERVICE_ROLE_KEY=eyJ... # BACKEND ONLY — never expose

# ── AWS ───────────────────────────────────────────────────
AWS_ACCESS_KEY_ID=AKIA...        # BACKEND ONLY
AWS_SECRET_ACCESS_KEY=...        # BACKEND ONLY
AWS_DEFAULT_REGION=us-east-1

# ── BACKEND ───────────────────────────────────────────────
BACKEND_PORT=8000
LOG_LEVEL=INFO

# ── SAFETY ────────────────────────────────────────────────
GLOBAL_AUTOMATION_ENABLED=false  # Default: off
DRY_RUN_MODE=true                # Default: dry-run
MAX_MONTHLY_BUDGET_USD=10.00
MAX_DAILY_SPEND_USD=2.00
MAX_ACTIONS_PER_DAY=5
ACTION_COOLDOWN_MINUTES=30
MAX_CW_API_CALLS_PER_HOUR=200

# ── ML ────────────────────────────────────────────────────
ML_MODEL_PATH=/models
ML_CONTAMINATION=0.05
ML_ANOMALY_THRESHOLD=-0.3

# ── FRONTEND (Vite prefix required) ───────────────────────
VITE_SUPABASE_URL=https://your-project.supabase.co
VITE_SUPABASE_ANON_KEY=eyJ...    # Safe to expose
VITE_BACKEND_URL=http://localhost:8000
```

**Frontend env vars MUST use `VITE_` prefix. Never prefix AWS or service_role vars with `VITE_`.**

---

## 15. Deployment Architecture

### Production

| Component | Platform | Why | Cost |
|---|---|---|---|
| **Frontend** | Vercel | Free tier for static React/Vite; auto-deploy from GitHub; HTTPS automatic | Free |
| **Backend (FastAPI + APScheduler + ML)** | Render (free/starter) | Python native support; persistent process for APScheduler; Docker support; 512MB RAM sufficient for IF at MVP scale | Free tier (spins down after inactivity — upgrade to $7/mo Starter if needed) |
| **Database** | Supabase | Managed PostgreSQL + Auth + Realtime; free tier: 500MB DB, 50MB file storage, 2 GB bandwidth | Free tier |
| **Model files** | Render disk (bind mount) OR S3 bucket (demo account) | Render free tier has ephemeral filesystem — use S3 for persistence | ~$0.00 (S3 free tier) |

**Render free tier limitation:** Service sleeps after 15 minutes of inactivity. APScheduler jobs will not run while sleeping. For demo purposes, use Render Starter ($7/month) or keep the backend always-on via a health check ping. [VERIFY: Render free tier sleep policy.]

**Alternative backend:** Railway.app (similar Python support, $5 credit/month free). Evaluate based on current pricing.

### Local-Only (Demo Mode)

For demo purposes, the entire system can run locally:
- Backend: `uvicorn app.main:app --reload`
- Frontend: `pnpm dev`
- DB: Hosted Supabase free project
- AWS: Real AWS account (free tier)

Total local demo cost: $0 (Supabase free, AWS free tier, no cloud deployment).

---

## 16. CI/CD

```yaml
# .github/workflows/ci.yml
name: CI

on: [push, pull_request]

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r backend/requirements.txt
      - run: ruff check backend/       # Linting
      - run: pytest backend/tests/unit backend/tests/ml -v
      # Integration tests skipped unless SUPABASE_URL secret set
      - run: pytest backend/tests/integration -v
        if: env.SUPABASE_URL != ''
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: cd frontend && pnpm install
      - run: cd frontend && pnpm lint
      - run: cd frontend && pnpm test

  deploy-frontend:
    needs: [backend, frontend]
    if: github.ref == 'refs/heads/main'
    # Vercel deploys automatically via GitHub integration — no step needed
    # Or: vercel --prod via Vercel CLI action

  deploy-backend:
    needs: [backend, frontend]
    if: github.ref == 'refs/heads/main'
    # Render deploys automatically via GitHub integration
```

**Cloud tests** (`@pytest.mark.cloud`) are NOT run in CI. They require real AWS credentials and are run manually before demo.

---

## 17. Package / Dependency List

### Backend — REQUIRED

```
fastapi
uvicorn[standard]
pydantic[email]
pydantic-settings
supabase              # supabase-py client
asyncpg               # raw async PostgreSQL for time-series queries
boto3
botocore
structlog
python-dotenv
python-jose[cryptography]   # JWT verification
apscheduler
scikit-learn
pandas
numpy
scipy
joblib
httpx                 # async HTTP (for health checks, supabase-py dependency)
```

### Backend — TESTING REQUIRED

```
pytest
pytest-asyncio
moto[all]
httpx                 # FastAPI async test client
pytest-mock
```

### Backend — OPTIONAL (add if time permits)

```
shap                  # ML explainability (V2 — anomaly feature contributions)
```

### Backend — DO NOT ADD YET

```
celery, redis, kombu  — no async queue requirement
sqlalchemy, alembic   — supabase-py + asyncpg replaces ORM
prophet               — insufficient data for MVP
tensorflow, torch     — overkill
```

### Frontend — REQUIRED

```json
{
  "react": "^18",
  "react-dom": "^18",
  "react-router-dom": "^6",
  "@supabase/supabase-js": "^2",
  "axios": "^1",
  "@tanstack/react-query": "^5",
  "recharts": "^2",
  "tailwindcss": "^3",
  "clsx": "^2",
  "lucide-react": "latest"
}
```

```json
"devDependencies": {
  "vite": "^5",
  "vitest": "^1",
  "@testing-library/react": "^14",
  "@testing-library/user-event": "^14",
  "msw": "^2",
  "typescript": "^5",
  "@types/react": "^18",
  "eslint": "^8",
  "prettier": "^3"
}
```

### Frontend — DO NOT ADD YET

```
redux, zustand, recoil   — TanStack Query covers server state; Context covers UI state
next.js                  — SSR unnecessary for internal dashboard
d3                       — Recharts is sufficient
socket.io                — Supabase Realtime covers realtime needs
framer-motion            — no animation requirement in PRD
```

---

## 18. Environment Variables

| Variable | Frontend Safe? | Backend Only? | Production Secret? | Notes |
|---|---|---|---|---|
| `VITE_SUPABASE_URL` | ✅ Yes | — | No | Public project URL |
| `VITE_SUPABASE_ANON_KEY` | ✅ Yes | — | No | Public, RLS-protected |
| `VITE_BACKEND_URL` | ✅ Yes | — | No | FastAPI base URL |
| `SUPABASE_URL` | — | ✅ Yes | No | Same value, no VITE_ prefix |
| `SUPABASE_SERVICE_ROLE_KEY` | ❌ NEVER | ✅ Yes | ✅ Yes | Full DB access — treat as password |
| `AWS_ACCESS_KEY_ID` | ❌ NEVER | ✅ Yes | ✅ Yes | IAM key |
| `AWS_SECRET_ACCESS_KEY` | ❌ NEVER | ✅ Yes | ✅ Yes | IAM secret |
| `AWS_DEFAULT_REGION` | — | ✅ Yes | No | e.g., `us-east-1` |
| `GLOBAL_AUTOMATION_ENABLED` | — | ✅ Yes | No | Default: `false` |
| `DRY_RUN_MODE` | — | ✅ Yes | No | Default: `true` |
| `MAX_MONTHLY_BUDGET_USD` | — | ✅ Yes | No | Default: `10.00` |
| `MAX_DAILY_SPEND_USD` | — | ✅ Yes | No | Default: `2.00` |
| `MAX_ACTIONS_PER_DAY` | — | ✅ Yes | No | Default: `5` |
| `ACTION_COOLDOWN_MINUTES` | — | ✅ Yes | No | Default: `30` |
| `MAX_CW_API_CALLS_PER_HOUR` | — | ✅ Yes | No | Default: `200` |
| `ML_MODEL_PATH` | — | ✅ Yes | No | Local filesystem path |
| `ML_CONTAMINATION` | — | ✅ Yes | No | Default: `0.05` |
| `ML_ANOMALY_THRESHOLD` | — | ✅ Yes | No | Default: `-0.3` |
| `LOG_LEVEL` | — | ✅ Yes | No | Default: `INFO` |

---

## 19. What We Are Explicitly NOT Using

| Technology | Why Not |
|---|---|
| **TimescaleDB** | Not available on Supabase managed PostgreSQL [VERIFY]. Standard PostgreSQL with indexes is sufficient at MVP scale (< 15K rows/day). |
| **InfluxDB** | Separate system from relational data. Doubles infrastructure. No benefit at this scale. |
| **MongoDB** | JSONB in PostgreSQL covers document needs. No document-native query patterns required. |
| **Redis** | No caching requirement (DB is fast enough at MVP scale). No pub/sub (Supabase Realtime covers it). No job queue (APScheduler is in-process). |
| **Celery** | Requires Redis broker. APScheduler runs in-process. No distributed workers needed. |
| **Kafka / Kinesis** | No event streaming requirement. 5-min polling is sufficient. CloudWatch already aggregates data. |
| **Kubernetes** | 1–4 developers, one backend service. Massive operational overhead for zero benefit. |
| **Prophet** | Requires weeks of seasonal data. MVP demo cannot guarantee this. IF + Z-score is sufficient. |
| **GraphQL** | REST API surface is small and well-defined. GraphQL adds schema management complexity. |
| **Elasticsearch** | No full-text search requirement. Audit log queries are simple timestamp+filter queries. |
| **LangChain / LLMs** | No natural language interface in PRD. Anomaly reasons are template-generated strings. |
| **Vector database** | No semantic search, no embedding requirement. |
| **Terraform** | Supabase is managed (no infra to provision). AWS demo resources provisioned via script. Overkill for student project. |
| **SQLAlchemy ORM** | Replaced by `supabase-py` for relational tables and `asyncpg` for time-series. Three DB abstraction layers are worse than two. |
| **Next.js** | Dashboard is an internal tool. SSR provides no SEO or performance benefit. Vite + React is simpler. |
| **D3.js** | Recharts provides sufficient charting capability. D3 is lower-level and requires more implementation work. |
| **Redux / Zustand** | TanStack Query manages server state. React Context handles global UI state. No complex client-side state requires a dedicated store. |

---

## 20. Final Stack

```
Frontend:
  React 18 + TypeScript + Vite 5
  Tailwind CSS + shadcn/ui
  Recharts
  Axios + TanStack Query v5
  @supabase/supabase-js (Auth + Realtime)
  React Router v6

Backend:
  Python 3.11 + FastAPI + Pydantic v2 + uvicorn
  supabase-py (PostgREST client for relational tables)
  asyncpg (raw SQL for time-series queries)
  boto3 + botocore (all AWS operations)
  APScheduler 3.x (AsyncIOScheduler)
  structlog
  python-dotenv + pydantic-settings
  python-jose (JWT verification)

Database:
  Supabase PostgreSQL (managed)
  — Standard PostgreSQL, no extensions
  — resource_metrics: time-indexed append-only table
  — All schema via Supabase CLI migrations

Auth:
  Supabase Auth (Email/Password)
  JWT verified in FastAPI via JWKS

Realtime:
  Supabase Realtime
  — anomalies table INSERT → dashboard push
  — optimization_actions table UPDATE → action status push

Row Level Security:
  Supabase RLS on all tables
  — anon: no access
  — authenticated: read + limited writes
  — service_role (backend only): full access

ML:
  scikit-learn (IsolationForest)
  scipy (Z-score fallback)
  pandas + NumPy (feature engineering)
  joblib (model serialization)
  — Runs in FastAPI backend process

Cloud SDK:
  boto3 (AWS-first)
  — EC2, Lambda, S3, RDS, EBS discovery
  — CloudWatch telemetry
  — EC2 stop/start, Lambda concurrency, tagging

Workers / Scheduling:
  APScheduler (in-process, same Python process as FastAPI)

Deployment:
  Frontend: Vercel (free tier)
  Backend:  Render (starter tier — $7/mo or free with sleep caveat)
  Database: Supabase (free tier)

CI/CD:
  GitHub Actions
  — ruff (lint) + pytest (unit + ml) + vitest (frontend)
  — Auto-deploy via Vercel + Render GitHub integration

Testing:
  Backend: pytest + pytest-asyncio + moto + httpx
  Frontend: Vitest + @testing-library/react + msw
  Cloud:    Manual real-AWS integration tests (not in CI)

Monitoring:
  structlog → stdout → Render/Vercel log viewer
  FastAPI /health endpoint
  Supabase dashboard (DB metrics)
```

---

## 21. Component Responsibility Matrix

| Component | Owns | Does NOT Own | Communicates With |
|---|---|---|---|
| **React Frontend** | Dashboard UI, user interactions, session state, Realtime subscriptions | No data, no cloud credentials, no ML, no DB writes (except approved actions + system config) | FastAPI (REST), Supabase Auth, Supabase Realtime |
| **FastAPI Backend** | REST API, policy evaluation, safety layer, action state machine, JWT verification, job coordination | ML model training schedule (delegated to APScheduler), Supabase schema, frontend rendering | Supabase (supabase-py + asyncpg), AWS (boto3), APScheduler (in-process) |
| **APScheduler** | Job scheduling, interval enforcement, job failure logging | Business logic (delegates to service functions) | FastAPI services (in-process function calls) |
| **ML Pipeline** | Feature engineering, IF training, IF inference, Z-score fallback, anomaly record creation | DB schema, API endpoints, cloud credentials | asyncpg (read metrics), supabase-py (write anomalies) |
| **AWSAdapter** | All boto3 calls, retry logic, error normalization | Business logic, policy decisions, DB | boto3 clients only |
| **Supabase PostgreSQL** | Data persistence — all tables | ML computation, cloud API calls, background jobs | FastAPI (asyncpg + supabase-py), Frontend (PostgREST with RLS) |
| **Supabase Auth** | User authentication, JWT issuance, session management | Authorization decisions (RLS handles that) | Frontend (@supabase/supabase-js), FastAPI (JWT verification via JWKS) |
| **Supabase Realtime** | WebSocket event delivery for table changes | Data writes, ML, business logic | Frontend (subscriber) |
| **Safety Layer** | Kill-switch enforcement, budget checks, action limits, dry-run mode | Policy decisions (that's the Policy Engine's job) | FastAPI action runner (in-process), Supabase system_config |

---

## 22. Traceability to PRD

| PRD Requirement | Technology | Component | Implementation |
|---|---|---|---|
| Discover EC2/Lambda/S3/RDS/EBS | boto3 | AWSAdapter + DiscoveryService | `describe_instances`, `list_functions`, `list_buckets`, etc. → Supabase `resources` table |
| Collect CloudWatch telemetry (5-min) | boto3 CloudWatch | TelemetryService + APScheduler | `get_metric_data` batched → Supabase `resource_metrics` |
| Store 90 days of metrics | Supabase PostgreSQL | `resource_metrics` table | Time-indexed append-only; daily cleanup job deletes rows > 90 days |
| Isolation Forest anomaly detection | scikit-learn | ML Pipeline | Daily training, 5-min inference → Supabase `anomalies` |
| Z-score cold-start fallback | scipy | ML Pipeline (baseline.py) | Triggers when < 12h of data; writes anomaly with `model_version=zscore_fallback` |
| Anomaly record with reason string | supabase-py | ML Pipeline → FastAPI | Written to Supabase `anomalies` with `reason`, `confidence`, `features_snapshot` |
| Policy engine evaluation | Python (in-process) | PolicyEngine service | JSONB conditions evaluated against anomaly + resource + system state |
| Safety layer (kill-switch, dry-run, limits) | Python + Supabase system_config | SafetyLayer service | Checks DB-stored config on every action evaluation |
| Stop idle EC2 (auto-execute, MEDIUM) | boto3 ec2 | ActionRunner | `ec2.stop_instances()` after policy + safety pass; state verified |
| Limit Lambda concurrency (auto-execute, MEDIUM) | boto3 lambda | ActionRunner | `lambda.put_function_concurrency()` |
| Apply tags (auto-execute, LOW) | boto3 ec2/lambda/s3 | ActionRunner | `ec2.create_tags()`, `lambda.tag_resource()`, `s3.put_bucket_tagging()` |
| HIGH-risk approval flow | FastAPI + React | ActionRunner + OptimizationCenter | Action stays `pending_approval`; frontend renders approve button; PATCH to FastAPI executes |
| Rollback (start EC2, restore concurrency) | boto3 | ActionRunner | Inverse API call stored as rollback_action; rollback button in dashboard |
| Full audit log | Supabase audit_logs | AuditLogger service | Written before + after every API call; never updated |
| Dashboard — cost trends, anomaly center, optimization | React + Recharts | Frontend components | FastAPI REST + Supabase PostgREST + Supabase Realtime |
| Real-time dashboard updates (new anomaly, action status) | Supabase Realtime | Frontend Realtime subscriber | `postgres_changes` on `anomalies` INSERT, `optimization_actions` UPDATE |
| Authentication | Supabase Auth | Frontend login + FastAPI JWT verification | Email/password login → JWT → FastAPI verifies on each request |
| Emergency stop (kill-switch API) | FastAPI + Supabase | `POST /system/emergency-stop` | Sets `system_config.GLOBAL_AUTOMATION_ENABLED=false` in Supabase |
| Dry-run mode | Backend SafetyLayer | All action execution paths | Checks `DRY_RUN_MODE` before any boto3 write call |
| Estimated cost calculation | Python (static pricing JSON) | CostEstimationService | Usage × pricing table → Supabase `cost_records` |
| Resource state tracking | boto3 + Supabase | DiscoveryService | Discovery diffs previous vs current state; writes state change to audit_log |
| ML model versioning | joblib + filesystem | ML Pipeline | Model saved as `/models/if_{type}_{date}.pkl`; last 5 retained |
| Action verification (post-execution) | boto3 + APScheduler | ActionRunner verify job | `describe_instances` until state matches expected; timeout 5 min |

---

## 23. Architecture Decisions

**ADR-S1: Why Supabase PostgreSQL**
```
DECISION:    Supabase managed PostgreSQL for all data storage
ALTERNATIVES: Raw PostgreSQL (self-hosted Docker), TimescaleDB, InfluxDB, Firebase
REASON:      Managed PostgreSQL eliminates DB operations. Auth and Realtime are bundled.
             At MVP scale (< 15K rows/day), standard PostgreSQL with indexes is sufficient.
             TimescaleDB is not available on Supabase managed instances [VERIFY].
TRADE-OFF:   Cannot install arbitrary PostgreSQL extensions (TimescaleDB, pg_cron).
             Mitigated by: time-series queries via standard SQL; cleanup via APScheduler.
```

**ADR-S2: Why FastAPI**
```
DECISION:    FastAPI as the backend API framework
ALTERNATIVES: Flask, Django, Express.js
REASON:      Native async (required for APScheduler + asyncpg + boto3 async calls).
             Pydantic v2 integration for validation. Auto-generated OpenAPI docs.
             Python ecosystem required for boto3 and scikit-learn — same process.
TRADE-OFF:   Slightly steeper learning curve than Flask for beginners.
```

**ADR-S3: Why Python for ML**
```
DECISION:    Python (scikit-learn) for ML in same backend process
ALTERNATIVES: Separate ML service (FastAPI ML microservice), R, Julia
REASON:      scikit-learn's IsolationForest is production-quality and well-maintained.
             Running in the same process eliminates network calls between API and ML.
             No GPU needed. If inference takes < 100ms, there is no reason to separate it.
TRADE-OFF:   ML training blocks the process if APScheduler runs it synchronously.
             Mitigation: run training in APScheduler's ThreadPoolExecutor.
```

**ADR-S4: Why AWS over GCP**
```
DECISION:    AWS-first for MVP
ALTERNATIVES: GCP-first, multi-cloud from day 1
REASON:      boto3 is the most mature cloud SDK. Free tier covers all MVP resources.
             EC2 stop/start is the simplest reversible automation action.
             GCP supported in V2 via CloudAdapter ABC.
TRADE-OFF:   GCP users cannot use MVP without implementing GCPAdapter.
```

**ADR-S5: Why Isolation Forest over Prophet**
```
DECISION:    IsolationForest (primary) + Z-score (cold-start fallback)
ALTERNATIVES: Prophet, LSTM, rolling threshold only
REASON:      Prophet requires 2+ weeks of daily data. Demo may run before this is available.
             IF works with days of data, handles multivariate features, is unsupervised.
             Z-score covers the first 12h (cold start) with zero training data.
TRADE-OFF:   IF is not seasonal-aware. Low-traffic hours may generate false positives.
             Mitigation: time_of_day as a feature input; contamination tuning.
```

**ADR-S6: Why one backend instead of microservices**
```
DECISION:    Single FastAPI process with in-process APScheduler and ML
ALTERNATIVES: Collector microservice, ML microservice, API microservice
REASON:      1–4 developers cannot maintain 3+ deployed services. Shared in-process state
             (model files, job state) eliminates serialization overhead.
             FastAPI + APScheduler in one process handles 5-resource MVP trivially.
TRADE-OFF:   CPU-intensive ML training may impact API latency during training window.
             Mitigation: train at 02:00 UTC (low traffic); run in thread pool.
```

**ADR-S7: Why APScheduler over alternatives**
```
DECISION:    APScheduler AsyncIOScheduler (in-process)
ALTERNATIVES: Celery, GitHub Actions, cron, Supabase pg_cron
REASON:      In-process means no additional services (no Redis, no worker dyno).
             AsyncIOScheduler integrates with FastAPI's event loop natively.
             5-minute telemetry polling and 15-minute discovery cannot use GitHub Actions
             (minimum 5-min interval + workflow startup time = unreliable).
TRADE-OFF:   If FastAPI process restarts, in-flight jobs are lost. Next cycle compensates.
```

**ADR-S8: Why Supabase Realtime is used (selectively)**
```
DECISION:    Realtime for anomaly INSERT and action status UPDATE only
ALTERNATIVES: Poll every 5 seconds, WebSocket from FastAPI
REASON:      These are the two events that matter during a live demo. Seeing an anomaly
             appear in real-time without refresh is high demo value. Polling for everything
             would require 5-second intervals and would still feel delayed.
             Supabase Realtime is already included — no additional infrastructure.
TRADE-OFF:   Supabase Realtime free tier has connection limits [VERIFY].
             For 1–4 simultaneous viewers, this is not a concern.
```

**ADR-S9: Why Redis is not used**
```
DECISION:    Redis excluded
REASON:      No caching requirement (PostgreSQL is fast enough at MVP scale).
             No pub/sub requirement (Supabase Realtime covers it).
             No job queue (APScheduler is in-process).
             Adding Redis requires a separate managed service (~$5–15/month) and
             a Redis client in the backend — two additions for zero concrete benefit.
TRADE-OFF:   None at this scale.
```

**ADR-S10: Why no dedicated time-series database**
```
DECISION:    Supabase PostgreSQL with time-indexed table (no TimescaleDB)
REASON:      TimescaleDB not available on Supabase managed instances [VERIFY].
             Standard PostgreSQL with (resource_id, time DESC) composite index handles
             < 15K rows/day trivially. Time-bucket aggregations expressible in standard SQL.
             Adding a second database (InfluxDB) doubles operational burden.
TRADE-OFF:   No automatic partitioning or compression. Manual retention cleanup required.
             Mitigation: daily APScheduler job deletes rows > 90 days.
```

---

## 24. Technical Risks

**RISK-1: Supabase free tier limits**
```
RISK:        Supabase free tier: 500MB DB, 50MB storage, 2GB bandwidth, Realtime connection limits.
IMPACT:      Demo could fail if limits are hit; metric data could fill free storage.
MITIGATION:  90-day retention cleanup job (daily). Monitor Supabase dashboard.
             Estimated 90-day storage: ~126MB — within 500MB limit.
             [VERIFY all current Supabase free tier limits before project start]
```

**RISK-2: Render free tier sleep**
```
RISK:        Render free tier sleeps after 15 min of inactivity. APScheduler stops.
IMPACT:      Telemetry collection stops when no user is active. Demo requires always-on.
MITIGATION:  Use Render Starter ($7/month) for demo period. OR use UptimeRobot
             (free) to ping /health endpoint every 5 min to prevent sleep.
             [VERIFY: Render sleep behavior on free vs paid tiers]
```

**RISK-3: TimescaleDB unavailability on Supabase**
```
RISK:        PRD assumed TimescaleDB. Supabase may not support it [VERIFY].
IMPACT:      Time-series queries are less optimized without hypertables.
MITIGATION:  Standard PostgreSQL with composite index. At MVP scale, no performance
             difference is measurable. If queries slow down, add table partitioning.
```

**RISK-4: Supabase Realtime event delivery reliability**
```
RISK:        Realtime may miss events if connection drops mid-demo.
IMPACT:      Anomaly doesn't appear in real-time — demo looks broken.
MITIGATION:  TanStack Query 5-second refetch as fallback. Realtime is enhancement,
             not primary delivery mechanism. Dashboard works without it.
```

**RISK-5: AWS boto3 credential security**
```
RISK:        AWS keys committed to git, exposed in build artifacts, or logged.
IMPACT:      CRITICAL — unauthorized cloud access, unexpected charges.
MITIGATION:  Pre-commit hook to scan for AWS key patterns (git-secrets or gitleaks).
             Render/GitHub Secrets for CI. structlog configured to redact env vars.
             IAM policy: least privilege, region-restricted, no destructive permissions.
```

**RISK-6: ML cold-start during demo**
```
RISK:        Demo run before 7 days of data — Isolation Forest not trained.
IMPACT:      Demo shows Z-score fallback, not IF. Less impressive technically.
MITIGATION:  Synthetic data injection script pre-populates 7+ days of data into
             Supabase before demo. Train IF on synthetic data. Anomaly injection
             then tests IF (not fallback). Rehearse with synthetic data.
```

**RISK-7: CloudWatch metric delay**
```
RISK:        CloudWatch standard metrics have up to 5-minute delay before appearing.
IMPACT:      First 5-min collection cycle after resource start may return empty data.
MITIGATION:  Handle empty metric responses gracefully (skip, don't write zero).
             Log "no data available yet" — not an error.
```

**RISK-8: Render + asyncpg connection pooling**
```
RISK:        Supabase PostgreSQL has connection limits (free tier: 60 connections [VERIFY]).
             asyncpg creates a connection pool. Multiple Render instances = connection exhaustion.
IMPACT:      DB connection errors under load.
MITIGATION:  MVP runs as single Render instance. asyncpg pool size = 5 connections.
             Use Supabase Transaction Pooler (pgbouncer) if connections become an issue.
             [VERIFY: Supabase connection limits by tier]
```

---

*End of CloudSentry Tech Stack Document v1.0*  
*All [VERIFY] items must be confirmed against official documentation before implementation begins.*  
*PRD v1.0 is the source of truth. This document does not add features beyond the PRD.*
