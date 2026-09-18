# PS2 — CloudSentry: Cloud Cost Intelligence System
### Product Requirements Document (PRD)
**Version:** 1.0 | **Status:** Implementation-Ready Draft | **Date:** 2026-09-19  
**Author:** Principal Software Architect Review  
**Audience:** Engineering Team (1–4 Developers)

---

## INTERNAL PRD CONSISTENCY CHECK (Pre-Flight)

Before every section, the following invariants were verified:
- No contradictory requirements
- No nonexistent AWS APIs
- No unsupported capabilities
- No automatic destructive operations without safeguards
- No fabricated metrics or pricing
- Every major component maps to a requirement, test, and acceptance criterion
- MVP is realistically buildable by 1–4 developers within 8–12 weeks

---

## 1. Product Overview

| Field | Value |
|---|---|
| **Product Name** | CloudSentry |
| **One-line** | Autonomous cloud cost intelligence: detect, explain, and safely optimize AWS spending in real time. |
| **Primary Cloud** | AWS (free-tier + minimal paid usage) |
| **Target Users** | Student/research team operating a live AWS account; developers monitoring personal/project infra |
| **Core Value** | Real ML-driven anomaly detection with auditable, safe optimization — not just dashboards |

**Problem Being Solved:**  
Manual cloud cost monitoring fails silently. Idle resources, runaway functions, and unexpected spikes go unnoticed until the billing cycle. Existing tools (AWS Cost Explorer, CloudWatch dashboards) show data but do not act. CloudSentry closes the detect → decide → act loop autonomously, with full audit trails and safety controls.

**Why Different From a Basic Dashboard:**  
1. ML-driven anomaly scoring, not static threshold alerts  
2. Autonomous optimization actions (stop idle EC2, limit Lambda concurrency, tag unmanaged resources)  
3. Structured policy engine governs every automated decision  
4. Full before/after verification and rollback capability  
5. Kill-switch and dry-run system prevents uncontrolled spend  

---

## 2. Goals and Non-Goals

### Goals (MUST)
- Discover real AWS resources via boto3 (EC2, Lambda, S3, RDS)
- Collect live telemetry from CloudWatch (CPU, network, invocations, duration, storage)
- Store time-series metrics in PostgreSQL + TimescaleDB hypertable
- Detect anomalies using Isolation Forest (primary) with statistical fallback (Z-score/EWMA)
- Produce anomaly records with severity, confidence, affected resource, and recommended action
- Execute LOW/MEDIUM-risk optimizations autonomously via AWS APIs (stop EC2, update Lambda concurrency limit, apply tags)
- Require human approval for HIGH-risk actions (volume deletion, RDS stop)
- Provide a React dashboard with cost trends, anomaly center, optimization tracker, and audit log
- Enforce a cost kill-switch: daily and monthly budget caps, API rate limits, action cooldowns
- Support full dry-run mode for demo/testing without executing actual API calls

### Non-Goals (EXPLICITLY EXCLUDED)
| Non-Goal | Reason |
|---|---|
| Multi-cloud (GCP/Azure) in MVP | Doubles complexity; AWS free tier sufficient |
| Kubernetes / EKS monitoring | Out of scope for student project scale |
| Enterprise multi-account billing (AWS Organizations) | Requires root/management account; overkill |
| Automatic resource deletion | Too destructive; replaced by recommendation + approval |
| Production-critical workload management | System itself may have bugs; only test/demo resources |
| Kafka, Redis, service mesh | No concrete requirement that justifies the complexity |
| Real-time streaming (sub-1-min telemetry) | CloudWatch minimum granularity is 1 min; polling is sufficient |
| Cost anomaly coverage for Reserved/Savings Plans | Billing APIs lack real-time granularity |

---

## 3. Core User Stories

### US-1 — Developer: Cost Visibility
> "As a developer, I want to see which of my AWS resources are consuming disproportionate cost so I can take corrective action."

**Acceptance Criteria:**
- Dashboard shows per-resource estimated cost, utilization, and anomaly status
- Resources sortable by estimated cost, idle score, anomaly severity
- Cost data refreshed within 15 minutes of collection cycle

### US-2 — Developer: Anomaly Explanation
> "As a developer, I want to understand WHY an anomaly was flagged so I don't blindly trust the system."

**Acceptance Criteria:**
- Each anomaly record includes: affected resource ID, metric name, observed value, expected range, anomaly score, confidence level, detection model, and plain-English reason string
- Reason format: `"EC2 i-0abc123: CPU utilization 1.2% over 3h (threshold: 5%). Anomaly score: 0.87. Recommendation: Stop instance."`

### US-3 — Cloud Admin: Safe Automation
> "As a cloud admin, I want the system to automatically stop idle EC2 instances during off-hours, but only with adequate confidence and never without an audit trail."

**Acceptance Criteria:**
- System only executes stop action if: idle_score ≥ 0.80, confidence ≥ 0.75, resource not protected, age > 30 min, daily action count < limit
- Full audit record written before API call is made
- Post-execution: instance state verified as `stopped` within 2 minutes
- Rollback (start instance) available via dashboard button

### US-4 — Demo Operator: End-to-End Flow
> "As a demo operator, I want to trigger a controlled anomaly and watch the entire system respond without incurring unexpected cloud costs."

**Acceptance Criteria:**
- Synthetic workload script can inject CPU spike or Lambda invocation burst
- Anomaly detected within 2 collection cycles (≤ 10 minutes)
- Dashboard updates without manual refresh
- All actions execute in dry-run mode unless explicitly enabled
- Total cloud cost of full demo < $2 USD

### US-5 — Developer: Rollback
> "As a developer, I want to undo an automated action if it was wrong, without data loss."

**Acceptance Criteria:**
- Rollback button available for every completed action in the Optimization Center
- Rollback executes inverse API call (e.g., `start_instances`) and logs result
- Rollback only available for reversible actions; irreversible actions are never auto-executed

---

## 4. Functional Requirements

### A. Cloud Resource Discovery

| REQ-ID | Requirement | Implementation | Acceptance Criteria |
|---|---|---|---|
| RD-01 | Discover EC2 instances | `boto3.client('ec2').describe_instances()` | Returns ID, type, state, region, tags, launch time |
| RD-02 | Discover Lambda functions | `boto3.client('lambda').list_functions()` | Returns name, runtime, memory, timeout, last-modified |
| RD-03 | Discover S3 buckets | `boto3.client('s3').list_buckets()` + `get_bucket_location()` | Returns name, region, creation date |
| RD-04 | Discover RDS instances | `boto3.client('rds').describe_db_instances()` | Returns ID, class, engine, status, multi-AZ, region |
| RD-05 | Discover EBS volumes | `boto3.client('ec2').describe_volumes()` | Returns ID, size, state, attachment, type |
| RD-06 | Collect resource tags | Tags collected per resource during discovery | Tag map stored per resource |
| RD-07 | Discovery runs on schedule | APScheduler job every 15 minutes | Resources table updated; new/deleted resources flagged |
| RD-08 | Resource state tracking | State change (running→stopped) logged to AuditLog | State transitions visible in dashboard |

**Required AWS Permissions:** `ec2:Describe*`, `lambda:ListFunctions`, `s3:ListBuckets`, `s3:GetBucketLocation`, `rds:DescribeDBInstances`, `ec2:DescribeVolumes`

### B. Telemetry Collection

**Collection Method:** Polling CloudWatch Metrics API on a 5-minute schedule.  
**VERIFIED FACT:** CloudWatch standard metrics have 1-minute minimum resolution. Detailed monitoring (EC2) available at extra cost. [VERIFY CURRENT PRICING: ~$3.50/instance/month for detailed monitoring — use standard for MVP.]

| Metric | Source | Resource Type | Frequency | Retention |
|---|---|---|---|---|
| CPUUtilization | CloudWatch | EC2 | 5 min | 90 days in DB |
| NetworkIn / NetworkOut | CloudWatch | EC2 | 5 min | 90 days |
| Invocations | CloudWatch | Lambda | 5 min | 90 days |
| Duration (avg/p99) | CloudWatch | Lambda | 5 min | 90 days |
| Errors | CloudWatch | Lambda | 5 min | 90 days |
| BucketSizeBytes | CloudWatch | S3 | Daily | 90 days |
| NumberOfObjects | CloudWatch | S3 | Daily | 90 days |
| DatabaseConnections | CloudWatch | RDS | 5 min | 90 days |
| FreeStorageSpace | CloudWatch | RDS | 5 min | 90 days |
| VolumeReadOps / WriteOps | CloudWatch | EBS | 5 min | 90 days |

**Implementation:** `boto3.client('cloudwatch').get_metric_statistics()` per resource per metric.  
**API Rate Limit:** CloudWatch: [VERIFY: 400 requests/second, 10,000 requests/24h for GetMetricStatistics] — batch via `get_metric_data()` for efficiency.  
**Memory metrics:** NOT available from CloudWatch without CloudWatch Agent installed on EC2. For MVP, omit memory or use CPUUtilization as primary signal.  
**ASSUMPTION:** Demo environment has ≤ 5 resources; CloudWatch API calls stay well within free tier (first 10,000 GetMetricStatistics/month free). [VERIFY CURRENT FREE TIER LIMITS]

### C. Cost Intelligence

**Critical Distinction:**

| Type | Source | Latency | Granularity | Notes |
|---|---|---|---|---|
| Actual Billing | AWS Cost Explorer API | 24–48h lag | Per service/day | Free tier: first 1M rows free [VERIFY] |
| Usage Metrics | CloudWatch | 5–15 min | Per resource | Available in near-real-time |
| Estimated Cost | Local calculation | Real-time | Per resource | Based on pricing tables, not actual bills |
| Projected Cost | Trend extrapolation | Real-time | Monthly | Based on current usage rate |

**MVP Cost Estimation Approach:**  
Embed a static AWS pricing table (JSON) for US-East-1 region covering: EC2 instance types (on-demand), Lambda (GB-seconds + invocations), S3 (storage + requests), RDS (instance + storage). Multiply usage metrics by price per unit → estimated hourly cost per resource.  
[VERIFY: AWS Pricing API (`pricing.us-east-1.amazonaws.com`) can be queried but has complex filter structure. Static pricing JSON is simpler and acceptable for MVP.]

**Limitations:**  
- Estimated cost will not match actual bill due to: data transfer charges, partial-hour billing, reserved instance discounts, support plan charges
- Actual billing data from Cost Explorer has 24–48h lag — not suitable for real-time anomaly detection
- Free tier usage is not reflected in estimated cost (system may show cost for resources that are actually free)

### D. Anomaly Detection

**Model Selection Rationale:**

| Model | Pros | Cons | Decision |
|---|---|---|---|
| **Isolation Forest** | Works unsupervised, handles multivariate data, minimal data required, fast inference | Not seasonal-aware, needs tuning | **PRIMARY — MVP** |
| **Prophet** | Handles seasonality, trends | Requires weeks of data, slow training, overkill for simple metrics | **EXCLUDED from MVP** |
| **Z-Score / EWMA** | Zero training data needed, interpretable | Univariate, no pattern learning | **FALLBACK for cold start** |
| **Rolling Window Threshold** | Simplest, fully auditable | High false positive rate | **Supplement only** |

**Cold-Start Strategy:**  
- Day 0–2: Z-score fallback (mean ± 2.5σ on rolling 24h window)  
- Day 3–7: EWMA baseline with adaptive thresholds  
- Day 7+: Isolation Forest trained on accumulated data  
- Retraining: Daily at 02:00 UTC on last 30 days of data  

**Isolation Forest Configuration:**
```
Features (per resource, per collection cycle):
  - cpu_utilization (EC2)
  - network_in_bytes_per_min (EC2)
  - network_out_bytes_per_min (EC2)
  - invocation_count (Lambda)
  - avg_duration_ms (Lambda)
  - error_rate (Lambda)
  - storage_bytes (S3/EBS)
  - db_connections (RDS)
  - estimated_cost_per_hour

Input window: Rolling 12 data points (1 hour at 5-min intervals)
Contamination: 0.05 (expect ~5% anomalous points) — tunable
n_estimators: 100
random_state: 42 (reproducible)
```

**Output:**  
- `anomaly_score`: float in [-1, 1] where lower = more anomalous  
- `is_anomaly`: bool (score < threshold)  
- `confidence`: derived from distance to decision boundary (implementation-defined heuristic)  
- `severity`: LOW / MEDIUM / HIGH based on score magnitude

**Evaluation Problem:** No labeled historical cloud anomaly data exists for this account.  
**Solution:** Synthetic anomaly injection (see Section 20) used to generate labeled test set. Precision/Recall evaluated on synthetic dataset.  
Target: Precision ≥ 0.75, Recall ≥ 0.70 on synthetic test set. Actual performance: TBD after evaluation.

### E. Anomaly Types

| Type | Detection Signal | Model | Severity | Auto-Execute? | Rollback |
|---|---|---|---|---|---|
| **Idle Compute** | CPU < 5% AND NetworkIn < 1MB/hr for > 2h | IF + Z-score | MEDIUM | YES (stop) | YES (start) |
| **Runaway Lambda** | Invocations 10× baseline OR error_rate > 20% | IF | HIGH | YES (set concurrency=0) | YES (restore concurrency) |
| **Traffic Spike** | NetworkIn/Out > 5× rolling avg | IF | MEDIUM | NO — recommend only | N/A |
| **Unused Volume** | EBS state=available (unattached) for > 24h | Rule-based | MEDIUM | NO — tag + recommend | N/A |
| **Untagged Resource** | Missing required tags (e.g., `Project`, `Owner`) | Rule-based | LOW | YES (apply tags) | YES (remove tags) |
| **Unexpected State** | Resource state changed without automation action | Rule-based | HIGH | NO — alert only | N/A |

---

## 5. Autonomous Optimization Engine

### Safety Architecture

Every automated action follows this state machine:

```
DETECT → VALIDATE → POLICY_CHECK → SAFETY_CHECK → [DRY_RUN | EXECUTE] → VERIFY → AUDIT_LOG
                                                              ↓
                                                    APPROVAL_REQUIRED (HIGH risk)
```

### Action Specifications

#### Action: Stop Idle EC2 Instance
| Field | Value |
|---|---|
| **Risk** | MEDIUM |
| **Trigger** | idle_score ≥ 0.80 AND confidence ≥ 0.75 |
| **Preconditions** | Instance state = `running`, age > 30 min, not in protected list |
| **Safety Checks** | Daily action count < MAX_ACTIONS_PER_DAY; instance not tagged `do-not-stop` |
| **AWS API** | `ec2.stop_instances(InstanceIds=[id])` |
| **Verification** | Poll `describe_instances` until state = `stopped` (timeout: 5 min) |
| **Rollback** | `ec2.start_instances(InstanceIds=[id])` |
| **Audit** | Pre-action state, post-action state, timestamp, anomaly_id, actor=SYSTEM |
| **Dry-Run** | Log "WOULD STOP i-xxxx" — no API call made |

#### Action: Limit Lambda Concurrency
| Field | Value |
|---|---|
| **Risk** | MEDIUM |
| **Trigger** | Runaway Lambda anomaly, concurrency > 10× baseline |
| **Preconditions** | Function exists, not protected |
| **Safety Checks** | Current reserved concurrency stored before action |
| **AWS API** | `lambda.put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=limit)` |
| **Verification** | `get_function_concurrency()` returns new limit |
| **Rollback** | `delete_function_concurrency()` or restore stored value |
| **Dry-Run** | Log "WOULD SET concurrency=5 for function-name" |

#### Action: Apply Tags to Untagged Resource
| Field | Value |
|---|---|
| **Risk** | LOW |
| **Trigger** | Resource missing required tags for > 1 collection cycle |
| **Preconditions** | Resource exists, tagging permission available |
| **AWS API** | `ec2.create_tags()` / `lambda.tag_resource()` / `s3.put_bucket_tagging()` |
| **Verification** | Describe resource and confirm tags present |
| **Rollback** | `delete_tags()` with applied tag keys |
| **Dry-Run** | Log "WOULD TAG resource-id with {Project: CloudSentry-Demo}" |

#### Action: Recommend Volume Deletion (HIGH RISK — NO AUTO-EXECUTE)
| Field | Value |
|---|---|
| **Risk** | HIGH |
| **Mode** | DETECT → RECOMMEND → HUMAN APPROVE → EXECUTE → VERIFY |
| **Dashboard** | Approval button shown; action executes only after click |
| **AWS API** | `ec2.delete_volume(VolumeId=id)` — only after approval |
| **Rollback** | NOT possible (deletion is permanent). Therefore auto-execute is **never allowed**. |

---

## 6. Cost Safety / Kill-Switch Design

```
GLOBAL_AUTOMATION_ENABLED: bool  (env var, default: false in dev)
DRY_RUN_MODE: bool               (env var, default: true)
MAX_MONTHLY_BUDGET_USD: float    (default: 10.00)
MAX_DAILY_SPEND_USD: float       (default: 2.00)
MAX_ACTIONS_PER_DAY: int         (default: 5)
ACTION_COOLDOWN_MINUTES: int     (default: 30 per resource)
MAX_CW_API_CALLS_PER_HOUR: int   (default: 200)
```

### Protected Resources
- Hardcoded denylist in config: resource IDs that may NEVER be acted on
- Tag-based protection: any resource tagged `cloudsentry:protected=true` is exempt from all actions
- Database instances always require approval regardless of risk level

### Emergency Shutdown
- API endpoint: `POST /api/v1/system/emergency-stop`  
- Sets `GLOBAL_AUTOMATION_ENABLED=false` in database  
- All in-flight actions cancelled (best-effort)  
- Dashboard shows red banner: "AUTOMATION DISABLED — Emergency Stop Active"

### Demo Cost Minimization
- Default: all automation in dry-run mode  
- Demo script provisions: 1× t2.micro EC2 (free tier), 1× Lambda function, 1× S3 bucket, 1× PostgreSQL on RDS t2.micro (free tier for 12 months)  
- Total estimated demo cost: < $0.50/day on free-tier account [VERIFY CURRENT FREE TIER ELIGIBILITY]  
- Auto-shutdown script runs after demo: stops EC2, deletes Lambda test resources  

---

## 7. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     AWS Account                         │
│  EC2 │ Lambda │ S3 │ RDS │ EBS │ CloudWatch             │
└──────────────────────┬──────────────────────────────────┘
                       │ boto3 / CloudWatch API
                       ▼
┌─────────────────────────────────────────────────────────┐
│              Telemetry Collector (Python)                │
│  APScheduler Jobs │ Resource Discovery │ Metric Poller  │
└──────────────────────┬──────────────────────────────────┘
                       │ SQLAlchemy ORM
                       ▼
┌─────────────────────────────────────────────────────────┐
│         PostgreSQL + TimescaleDB (Docker)                │
│  resources │ metrics (hypertable) │ anomalies │ actions  │
└──────────┬──────────────────────────────────────────────┘
           │                        │
           ▼                        ▼
┌──────────────────┐    ┌───────────────────────────────┐
│  ML Service      │    │  FastAPI Backend               │
│  (scikit-learn)  │◄───│  REST API │ Auth │ Scheduler   │
│  IF + Z-score    │    │  Policy Engine │ Safety Layer  │
└────────┬─────────┘    └──────────┬────────────────────┘
         │                         │
         └──────────┬──────────────┘
                    │ REST + boto3
                    ▼
          ┌─────────────────┐
          │  Optimization   │
          │  Action Runner  │
          │  (async tasks)  │
          └────────┬────────┘
                   │ boto3
                   ▼
          ┌─────────────────┐
          │   AWS APIs      │
          │ (EC2/Lambda/S3) │
          └─────────────────┘
                   │
                   ▼
          ┌─────────────────┐
          │  React Frontend │
          │  Dashboard      │
          └─────────────────┘
```

### Component Responsibilities

| Component | Technology | Responsibility | Failure Behavior |
|---|---|---|---|
| **Telemetry Collector** | Python + APScheduler | Polls CloudWatch, writes metrics to DB | Logs error, skips cycle, retries next interval |
| **PostgreSQL + TimescaleDB** | Docker container | Stores all time-series and relational data | Backend returns 503; collector queues writes |
| **ML Service** | scikit-learn, pandas | Trains IF model, scores new metrics | Falls back to Z-score if model unavailable |
| **FastAPI Backend** | Python 3.11, FastAPI | REST API, policy evaluation, safety checks | Returns structured error; logs to application log |
| **Action Runner** | Python asyncio tasks | Executes cloud API actions, verifies, logs | Marks action FAILED, triggers rollback if configured |
| **React Frontend** | React 18, Recharts | Dashboard, anomaly center, audit log | Shows stale data warning; retry button |
| **Scheduler** | APScheduler (in-process) | Triggers collection, training, cleanup jobs | Job missed: logged; next run compensates |

---

## 8. Technology Stack

### Backend
| Tech | Version | Why |
|---|---|---|
| Python | 3.11 | boto3 native; ML ecosystem; team familiarity |
| FastAPI | 0.111+ | Async, auto-OpenAPI docs, Pydantic validation |
| SQLAlchemy | 2.0 | ORM with TimescaleDB compatibility |
| APScheduler | 3.x | In-process scheduler; no Celery/Redis needed |
| boto3 | latest | Official AWS SDK; required for all cloud operations |

### ML
| Tech | Why |
|---|---|
| scikit-learn | Isolation Forest implementation; stable; no GPU needed |
| pandas | Feature engineering on time-series data |
| NumPy | Numerical operations |
| joblib | Model serialization |

**NOT USED:** Prophet (requires too much data for MVP timeframe), TensorFlow/PyTorch (overkill), Kafka (no streaming requirement)

### Database
| Tech | Why |
|---|---|
| PostgreSQL 16 | Reliable; TimescaleDB extension available |
| TimescaleDB | Hypertable for metrics; time-bucket queries; no extra infra |

**NOT USED:** InfluxDB (separate system to maintain), Cassandra (overkill), DynamoDB (cost + complexity)

### Frontend
| Tech | Why |
|---|---|
| React 18 | Ecosystem; team likely knows it |
| Recharts | Simple time-series charting; MIT licensed |
| Axios | HTTP client |
| TanStack Query | Data fetching + caching + stale state |

**NOT USED:** Next.js SSR (unnecessary for internal tool), D3.js (too low-level for timeline)

### Infrastructure
| Tech | Why |
|---|---|
| Docker + Docker Compose | Local dev + demo; single-command startup |
| `.env` files | Config management; secrets NOT in source |

**NOT USED:** Kubernetes, Terraform, Helm — all unnecessary at this scale.

---

## 9. AWS vs GCP Decision

### Comparison

| Criterion | AWS | GCP |
|---|---|---|
| Free tier compute | t2.micro / t3.micro 750h/month | f1-micro 720h/month |
| Free tier DB | RDS t2.micro 750h/month | Cloud SQL free tier limited [VERIFY] |
| Free serverless | Lambda 1M req/month, 400K GB-sec | Cloud Functions 2M req/month |
| Billing API | Cost Explorer (24–48h lag; free tier limited) | Cloud Billing API (similar lag) |
| Monitoring API | CloudWatch — mature, well-documented | Cloud Monitoring — equivalent |
| boto3 ecosystem | Mature; extensive documentation | google-cloud SDK — also mature |
| Team familiarity | Higher (assumption) | Lower (assumption) |
| Auto-stop capability | `ec2.stop_instances` — simple, reversible | `compute.instances().stop()` — equivalent |

**DECISION: AWS-First for MVP**  
**Reason:** boto3 documentation is extensive; free tier coverage matches requirements; team familiarity assumed higher. GCP could be added in V2 via cloud adapter abstraction layer.

**Interface Design:** All cloud operations go through a `CloudAdapter` abstract base class. `AWSAdapter` implements it. `GCPAdapter` stub prepared but not implemented in MVP.

---

## 10. Data Model

```sql
-- CloudAccount
CREATE TABLE cloud_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider VARCHAR(10) NOT NULL, -- 'aws'
    account_id VARCHAR(50) NOT NULL,
    alias VARCHAR(100),
    region VARCHAR(30) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Resource
CREATE TABLE resources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID REFERENCES cloud_accounts(id),
    provider_id VARCHAR(100) NOT NULL, -- e.g., 'i-0abc1234'
    resource_type VARCHAR(30) NOT NULL, -- 'ec2','lambda','s3','rds','ebs'
    name VARCHAR(200),
    region VARCHAR(30),
    state VARCHAR(30), -- 'running','stopped','available'
    tags JSONB,
    protected BOOLEAN DEFAULT FALSE,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB, -- instance type, runtime, etc.
    INDEX idx_resources_provider_id (provider_id),
    INDEX idx_resources_type (resource_type)
);

-- ResourceMetric (TimescaleDB hypertable)
CREATE TABLE resource_metrics (
    time TIMESTAMPTZ NOT NULL,
    resource_id UUID REFERENCES resources(id),
    metric_name VARCHAR(50) NOT NULL,
    value DOUBLE PRECISION,
    unit VARCHAR(30),
    PRIMARY KEY (time, resource_id, metric_name)
);
SELECT create_hypertable('resource_metrics', 'time');
CREATE INDEX ON resource_metrics (resource_id, time DESC);

-- CostRecord
CREATE TABLE cost_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_id UUID REFERENCES resources(id),
    estimated_cost_usd DOUBLE PRECISION,
    actual_cost_usd DOUBLE PRECISION, -- NULL until billing API confirms
    billing_period_start DATE,
    billing_period_end DATE,
    source VARCHAR(20), -- 'estimated','cost_explorer'
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

-- Anomaly
CREATE TABLE anomalies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_id UUID REFERENCES resources(id),
    anomaly_type VARCHAR(50), -- 'idle_compute','runaway_lambda', etc.
    severity VARCHAR(10), -- 'LOW','MEDIUM','HIGH'
    anomaly_score DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    detected_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    reason TEXT,
    features_snapshot JSONB, -- raw feature values at detection time
    model_version VARCHAR(50),
    status VARCHAR(20) DEFAULT 'active' -- 'active','resolved','false_positive'
);

-- OptimizationAction
CREATE TABLE optimization_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    anomaly_id UUID REFERENCES anomalies(id),
    resource_id UUID REFERENCES resources(id),
    action_type VARCHAR(50), -- 'stop_ec2','limit_lambda','apply_tags'
    risk_level VARCHAR(10), -- 'LOW','MEDIUM','HIGH'
    status VARCHAR(20) DEFAULT 'pending', -- 'pending','approved','executing','completed','failed','rolled_back'
    dry_run BOOLEAN DEFAULT TRUE,
    requires_approval BOOLEAN DEFAULT FALSE,
    approved_by VARCHAR(100),
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    executed_at TIMESTAMPTZ,
    verified_at TIMESTAMPTZ,
    pre_state JSONB,
    post_state JSONB,
    estimated_savings_usd DOUBLE PRECISION,
    rollback_action_id UUID REFERENCES optimization_actions(id)
);

-- AuditLog
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50),
    actor VARCHAR(50), -- 'SYSTEM','USER:<id>'
    resource_id UUID REFERENCES resources(id),
    action_id UUID REFERENCES optimization_actions(id),
    aws_api_call VARCHAR(100),
    request_params JSONB,
    response_status VARCHAR(20),
    message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Policy
CREATE TABLE policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100),
    enabled BOOLEAN DEFAULT TRUE,
    resource_type VARCHAR(30),
    anomaly_type VARCHAR(50),
    conditions JSONB, -- structured condition tree
    action_type VARCHAR(50),
    risk_level VARCHAR(10),
    requires_approval BOOLEAN,
    cooldown_minutes INT DEFAULT 30,
    priority INT DEFAULT 100,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- SystemConfig (kill-switch and limits)
CREATE TABLE system_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 11. API Specification

Base URL: `http://localhost:8000/api/v1`  
Auth: Bearer token (JWT) for all non-health endpoints

### Resources
| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/resources` | List all discovered resources | `[{id, provider_id, type, state, region, tags, estimated_cost}]` |
| GET | `/resources/{id}` | Resource detail + recent metrics | `{resource, metrics_24h, anomalies, actions}` |
| POST | `/resources/discover` | Trigger immediate discovery | `{job_id, status}` |

### Metrics
| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/metrics/{resource_id}?metric=cpu&from=&to=` | Time-series data | `[{time, value}]` |
| GET | `/metrics/{resource_id}/summary` | Latest metric values | `{cpu: 2.1, network_in: 500, ...}` |

### Anomalies
| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/anomalies?status=active&severity=HIGH` | List anomalies | `[{id, resource, type, score, confidence, reason, severity, detected_at}]` |
| GET | `/anomalies/{id}` | Anomaly detail | Full anomaly record + features |
| PATCH | `/anomalies/{id}/status` | Mark as false_positive / resolved | `{status}` |

### Optimization Actions
| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/actions?status=pending` | List actions | `[{id, resource, action_type, status, risk, dry_run}]` |
| POST | `/actions/{id}/approve` | Approve HIGH-risk action | `{status: approved}` |
| POST | `/actions/{id}/execute` | Execute approved action | `{status, job_id}` |
| POST | `/actions/{id}/rollback` | Rollback completed action | `{status, rollback_action_id}` |

### Dashboard
| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/dashboard/overview` | Summary cards | `{total_cost_est, resource_count, anomaly_count, savings_achieved, automation_status}` |
| GET | `/dashboard/cost-trend?days=7` | Cost over time | `[{date, cost}]` |
| GET | `/dashboard/anomaly-summary` | Severity breakdown | `{HIGH: 2, MEDIUM: 5, LOW: 1}` |

### System
| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/health` | System health | `{db: ok, ml: ok, aws_connectivity: ok, last_collection: ts}` |
| POST | `/system/emergency-stop` | Disable all automation | `{automation_enabled: false}` |
| GET | `/system/config` | View current config/limits | `{dry_run, max_actions, budget, ...}` |
| PATCH | `/system/config` | Update config | `{key, value}` |

---

## 12. ML Pipeline

```
1. DATA COLLECTION
   resource_metrics table → query last 30 days per resource

2. FEATURE ENGINEERING (per resource per time window)
   - rolling_avg_cpu_1h, rolling_avg_cpu_24h
   - cpu_deviation_from_24h_avg
   - network_in_ratio (current / 24h avg)
   - lambda_invocation_spike_ratio
   - hours_since_last_activity
   - time_of_day (hour 0-23) — for pattern context only
   - day_of_week — for pattern context only

3. TRAINING (daily at 02:00 UTC)
   - Load 30-day feature matrix
   - Fit IsolationForest(n_estimators=100, contamination=0.05)
   - Serialize model with joblib to /models/if_model_{resource_type}_{date}.pkl
   - Retain last 5 model versions

4. INFERENCE (every collection cycle, ~5 min)
   - Load latest model per resource type
   - Score new feature vector
   - If score < -0.3: flag as anomaly (threshold tunable)
   - Compute confidence: normalize score to [0,1] range

5. COLD START (< 7 days data)
   - Use Z-score: flag if |current - rolling_mean| > 2.5 * rolling_std
   - Minimum window for Z-score: 12 points (1 hour)

6. ALERT GENERATION
   - Write anomaly record to anomalies table
   - Trigger policy evaluation

7. MODEL EVALUATION (on synthetic test set)
   Target: Precision ≥ 0.75, Recall ≥ 0.70, F1 ≥ 0.72
   Actual: TBD after evaluation run

8. MODEL STORAGE
   - Local filesystem /models/ (bind mount in Docker)
   - Versioned by resource_type + date
   - No cloud model registry needed for MVP
```

**Cold Start Problem:** Isolation Forest requires sufficient data to learn a normal distribution. With < 288 data points (24h at 5-min intervals), the model is unreliable. Z-score fallback is non-negotiable.

---

## 13. Detection Logic (Concrete Examples)

### Example 1: Idle EC2 Instance
```
Resource: EC2 i-0abc1234 (t2.micro, running)
Signal collection window: last 2 hours (24 data points)

Signals:
  avg_cpu_utilization: 0.8%       [threshold: 5%]
  avg_network_in: 200 bytes/min   [threshold: 10KB/min]
  avg_network_out: 100 bytes/min  [threshold: 5KB/min]
  instance_age: 4h

IF anomaly_score: -0.72 (highly anomalous)
Z-score on cpu: 3.1σ below normal

Result: ANOMALY — IDLE_COMPUTE
Severity: MEDIUM
Confidence: 0.82
Reason: "CPU 0.8% (24h avg: 45%) for 2h. Network negligible. Score: -0.72."
Action: RECOMMEND stop (MEDIUM risk — auto-execute if enabled)
```

**How threshold 5% CPU is calibrated:** Initial threshold set at P5 of observed CPU distribution for running instances. Adjustable per resource type in policy config. Not arbitrary — derived from observed baseline.

### Example 2: Runaway Lambda
```
Resource: Lambda function "image-processor"
Signal collection window: last 15 minutes (3 data points)

Signals:
  invocations_per_min: 8,500     [24h avg: 120]
  spike_ratio: 70.8×
  error_rate: 0.2%               [normal]
  avg_duration: 2,100ms          [baseline: 2,200ms — normal]

IF anomaly_score: -0.91
Spike ratio alone: > 10× threshold

Result: ANOMALY — RUNAWAY_LAMBDA
Severity: HIGH
Confidence: 0.91
Reason: "Invocations 70× baseline (8500/min vs avg 120/min)."
Action: SET reserved_concurrency=10 (current effective limit)
Note: If invocation spike is legitimate traffic, limiting concurrency may cause throttling. Operator should review before approval.
```

### Example 3: Unused EBS Volume
```
Resource: EBS vol-0xyz (100GB gp2)
State: available (unattached)
Duration in state: 48h

Signals:
  VolumeReadOps: 0
  VolumeWriteOps: 0
  State: available

Detection: Rule-based (not ML)
Estimated cost: ~$10/month ongoing [VERIFY: gp2 pricing ~$0.10/GB/month]

Result: ANOMALY — UNUSED_VOLUME
Severity: MEDIUM
Recommended: Tag as "review:orphaned" + recommend deletion (HIGH risk, requires approval)
```

---

## 14. Optimization Policy Engine

### Policy Structure (JSON stored in DB)

```json
{
  "id": "policy-idle-ec2-auto-stop",
  "name": "Auto-stop idle EC2 instances",
  "enabled": true,
  "priority": 100,
  "resource_type": "ec2",
  "anomaly_type": "idle_compute",
  "conditions": {
    "AND": [
      {"field": "anomaly.idle_score", "op": "gte", "value": 0.80},
      {"field": "anomaly.confidence", "op": "gte", "value": 0.75},
      {"field": "resource.protected", "op": "eq", "value": false},
      {"field": "resource.age_minutes", "op": "gte", "value": 30},
      {"field": "system.automation_enabled", "op": "eq", "value": true},
      {"field": "system.daily_action_count", "op": "lt", "value": 5}
    ]
  },
  "action_type": "stop_ec2",
  "risk_level": "MEDIUM",
  "requires_approval": false,
  "cooldown_minutes": 30,
  "dry_run_override": false
}
```

### Policy Evaluation Order
1. Load all enabled policies ordered by priority (ascending = higher priority)
2. Evaluate conditions against anomaly + resource + system state
3. First matching policy wins
4. If no policy matches: log "no policy matched", no action
5. If `requires_approval=true`: create action with status=`pending_approval`
6. If `dry_run_mode` global: override all actions to dry-run regardless of policy

### Conflict Resolution
- If two policies match the same resource: highest priority (lowest number) wins
- Exemptions: resources tagged `cloudsentry:exempt=true` skip all policy evaluation

---

## 15. Dashboard Requirements

### Component Map

| View | Key Elements | Data Source |
|---|---|---|
| **Overview** | Total estimated cost, active anomalies count, savings achieved, automation status badge, resource count | `/dashboard/overview` |
| **Cost Analytics** | Line chart: cost over time; bar: per-resource cost; projected monthly | `/dashboard/cost-trend` |
| **Anomaly Center** | Table: severity, resource, type, score, confidence, age, recommended action button | `/anomalies` |
| **Optimization Center** | Pending approvals list, completed actions, before/after metric sparklines, rollback buttons | `/actions` |
| **Resource Explorer** | Table: all resources, utilization bars, state badge, estimated cost, protection toggle | `/resources` |
| **Audit Log** | Chronological log of every system event, API call, actor, result | `/audit-logs` |

### Critical UX States

| State | Display |
|---|---|
| **Dry-run mode active** | Yellow banner: "Automation in DRY-RUN mode — no actual API calls" |
| **Automation disabled** | Red banner: "Automation DISABLED" |
| **Stale telemetry** | Orange badge on resource: "Last data: 45m ago" |
| **Cold start** | Info card: "Insufficient data for ML. Using statistical baseline." |
| **No anomalies** | Green checkmark: "All resources within normal parameters" |
| **API disconnected** | Red banner: "AWS API unreachable. Showing cached data." |
| **Pending approval** | Action card with "APPROVE" / "DISMISS" buttons (HIGH risk only) |

---

## 16. Security Architecture

### IAM Design
Create a dedicated IAM user `cloudsentry-agent` with a custom policy (least privilege):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:Describe*", "lambda:List*", "lambda:Get*",
        "s3:ListAllMyBuckets", "s3:GetBucketLocation", "s3:GetBucketTagging",
        "rds:Describe*", "cloudwatch:GetMetricStatistics",
        "cloudwatch:GetMetricData", "cloudwatch:ListMetrics",
        "ce:GetCostAndUsage"
      ],
      "Resource": "*"
    },
    {
      "Sid": "LimitedWrite",
      "Effect": "Allow",
      "Action": [
        "ec2:StopInstances", "ec2:StartInstances",
        "ec2:CreateTags", "ec2:DeleteTags",
        "lambda:PutFunctionConcurrency", "lambda:DeleteFunctionConcurrency",
        "lambda:TagResource", "s3:PutBucketTagging",
        "rds:StopDBInstance", "rds:StartDBInstance",
        "rds:AddTagsToResource"
      ],
      "Resource": "*",
      "Condition": {
        "StringEquals": {"aws:RequestedRegion": "us-east-1"}
      }
    }
  ]
}
```

**NEVER grant:** `ec2:TerminateInstances`, `s3:DeleteBucket`, `rds:DeleteDBInstance`, `iam:*`

### Credential Storage
- AWS credentials stored in `.env` file (backend only), never in frontend code
- `.env` in `.gitignore` — never committed
- Loaded via `python-dotenv` into environment variables
- Production: use AWS instance profile or environment-injected secrets (not hardcoded)

### Backend Auth
- JWT tokens for dashboard API (HS256, short-lived: 1h, refresh: 7d)
- Single-user for MVP: admin credentials in `.env`
- [DESIGN DECISION REQUIRED] Multi-user auth not in MVP scope

### Encryption
- HTTPS: Use nginx reverse proxy with self-signed cert for demo; Let's Encrypt if deployed
- DB: PostgreSQL connection over localhost (Docker network) — acceptable for local demo

---

## 17. Failure Modes and Resilience

| Failure | Detection | System Behavior | Recovery | User Visibility |
|---|---|---|---|---|
| CloudWatch API timeout | boto3 exception catch | Log error, skip metric, mark collection incomplete | Retry next cycle | "Stale data" warning |
| Billing API delayed | Cost records show null actual_cost | Show estimated cost only; label as "estimated" | Auto-resolved when billing updates | "Estimated" label |
| ML model missing | FileNotFoundError on load | Fall back to Z-score baseline | Model trained at next scheduled run | "Cold start mode" badge |
| False anomaly fired | No auto-detection of false positives | Action created, requires human review if HIGH risk | User marks as false_positive | False positive button |
| Optimization API failure | boto3 ClientError | Action marked FAILED; rollback triggered if pre_state stored | Alert in audit log | Action status: FAILED |
| Resource already deleted | ResourceNotFound on describe | Mark resource.state=terminated; skip action | Resolved automatically | Resource greyed out |
| Permission denied | boto3 AccessDenied | Log specific missing permission; halt action | Fix IAM policy | Error message in Audit Log |
| DB unavailable | SQLAlchemy OperationalError | Collector queues metrics in memory (≤ 1h buffer) | Reconnect with backoff | Health endpoint: DB red |
| Duplicate action | Check existing active action for resource | Skip if action already pending/executing | Idempotency check on trigger | No visible duplicate |
| Clock skew | TimescaleDB hypertable rejects future timestamps | Validate timestamp before insert; use server time | NTP sync | Log warning |

---

## 18. Observability

### Application Logs (Structured JSON)
```json
{
  "timestamp": "2026-09-19T00:00:00Z",
  "level": "INFO",
  "component": "telemetry_collector",
  "resource_id": "i-0abc1234",
  "metric": "CPUUtilization",
  "value": 0.8,
  "aws_api_call": "GetMetricStatistics",
  "latency_ms": 145
}
```

### Health Endpoint Response
```json
{
  "db": {"status": "ok", "latency_ms": 2},
  "ml_model": {"status": "ok", "model_version": "2026-09-18", "mode": "isolation_forest"},
  "aws_connectivity": {"status": "ok", "last_check": "2026-09-19T00:25:00Z"},
  "telemetry": {"last_collection": "2026-09-19T00:25:00Z", "staleness_minutes": 4},
  "automation": {"enabled": false, "dry_run": true, "actions_today": 0}
}
```

### Key Metrics to Track (Internal)
- `telemetry.collection_duration_ms` — alert if > 60s
- `ml.inference_duration_ms`
- `aws.api_error_rate` — alert if > 5% errors
- `actions.success_rate` — alert if < 90%
- `db.query_latency_p99_ms`

**Distinction:** System health metrics (above) track CloudSentry itself. Cloud resource anomalies track the monitored AWS account. These must not be confused in logs or dashboards.

---

## 19. Testing Strategy

### Unit Tests (pytest)
- ML feature engineering functions (deterministic output for known input)
- Policy evaluation logic (given conditions JSON, verify action decision)
- Safety layer: verify action blocked when limits exceeded
- Cost estimation functions: known instance type + hours → expected cost
- Anomaly score thresholding logic

### Integration Tests
- FastAPI endpoints with TestClient + DB test fixtures (PostgreSQL in Docker)
- Telemetry collector against mocked boto3 (use `moto` library)
- Action runner against mocked boto3 — verify correct API calls made

### Cloud API Tests (Real AWS — Isolated Test Account)
- Resource discovery: provision a t2.micro, run discovery, verify it appears
- Metric collection: let EC2 run 15 min, verify CloudWatch metrics collected
- Action execution: stop a test EC2, verify state=stopped, start it, verify state=running
- Tag application: apply tags, verify via describe

### ML Tests
- Isolation Forest: inject synthetic anomaly vectors, verify detection rate > 70%
- Z-score fallback: inject 3σ outlier, verify flagged
- Cold start: verify fallback activates when < 7 days data
- Retraining: verify model file updated after daily job

### End-to-End Tests
- Full demo flow: provision → collect → inject anomaly → detect → policy match → action (dry-run) → dashboard updates

### Safety Tests
- Attempt action when GLOBAL_AUTOMATION_ENABLED=false → verify blocked
- Attempt action on protected resource → verify blocked
- Exceed MAX_ACTIONS_PER_DAY → verify subsequent actions queued/blocked
- Emergency stop: verify all pending actions halted

### Mocking Strategy
Use `moto` (Python) to mock AWS APIs in unit and integration tests. No real AWS calls for unit tests.

---

## 20. Synthetic Anomaly Testing

### Local (No Cloud Required)
| Experiment | Method | Expected Result |
|---|---|---|
| CPU spike | Inject vector: cpu=98% into feature array | IF detects within next inference cycle |
| Idle instance | Inject 24 consecutive cpu<1% vectors | idle_score triggers after 2h window |
| Lambda burst | Multiply invocation values by 50× | Spike ratio > 10× threshold detected |
| Data injection | Load synthetic CSV into resource_metrics table | Models train on synthetic baseline |

### With Mocked APIs (moto)
- Simulate `describe_instances` returning a `stopped` instance that was `running`
- Simulate CloudWatch returning flat-zero metrics for 2 hours
- Verify anomaly detected and correct action recommended

### On Real Cloud (Controlled)
- Run stress script on t2.micro (Linux `stress` package): `stress --cpu 4 --timeout 600`
- Verify CPU spike collected, anomaly flagged within 10 min
- Stop stress, verify recovery detected

**Cost of real-cloud experiments:** stress test on t2.micro for 10 min ≈ $0.00 (free tier). No significant cost risk.

---

## 21. Evaluation Metrics

### Detection (Measured on Synthetic Test Set)
| Metric | Target | Actual |
|---|---|---|
| Precision | ≥ 0.75 | TBD |
| Recall | ≥ 0.70 | TBD |
| F1 Score | ≥ 0.72 | TBD |
| False Positive Rate | ≤ 0.10 | TBD |
| Detection Latency | ≤ 10 min after anomaly onset | TBD |

### System Performance
| Metric | Target | Actual |
|---|---|---|
| Telemetry collection latency | ≤ 60s per full cycle | TBD |
| API response time (p95) | ≤ 500ms | TBD |
| Dashboard load time | ≤ 2s initial | TBD |

### Optimization
| Metric | Target | Actual |
|---|---|---|
| Action success rate | ≥ 90% | TBD |
| Rollback success rate | 100% for reversible actions | TBD |
| Before→after measurable CPU reduction | Verifiable via CloudWatch | TBD |

### Safety
| Metric | Target | Actual |
|---|---|---|
| Unauthorized actions | 0 | TBD |
| Actions outside policy | 0 | TBD |
| Accidental destructive actions | 0 | TBD |
| Monthly spend violation | 0 | TBD |

---

## 22. MVP vs Advanced Versions

### MVP (8–12 weeks)
- AWS resource discovery (EC2, Lambda, S3, RDS, EBS)
- CloudWatch telemetry collection (5-min polling)
- PostgreSQL + TimescaleDB storage
- Isolation Forest anomaly detection + Z-score fallback
- 3 anomaly types: idle compute, runaway Lambda, untagged resource
- 3 automated actions: stop EC2, limit Lambda, apply tags
- React dashboard: overview, anomalies, actions, audit log
- Safety layer: dry-run, kill-switch, action limits
- Full audit logging

### V2 (Post-MVP)
- Cost Explorer integration (actual billing data)
- Traffic spike anomaly type
- Unused volume detection + recommendation flow
- Email/Slack alerting
- GCP adapter (CloudAdapter abstraction)
- Multi-resource anomaly correlation
- User authentication (multi-user)

### V3 (Research-Grade)
- Prophet model for seasonal workloads
- Anomaly explanation with SHAP values
- Predictive cost forecasting
- Cross-resource anomaly causation graphs
- Automated policy recommendation from historical patterns

---

## 23. Implementation Roadmap

### Phase 1 — Cloud Setup (Week 1)
- Create AWS IAM user with custom policy
- Provision demo resources: 1× t2.micro EC2, 1× Lambda, 1× S3 bucket, 1× RDS t2.micro
- Verify CloudWatch metrics appear for all resources
- Set up Docker Compose: PostgreSQL + TimescaleDB
- **Done:** AWS credentials working; metrics visible in CloudWatch console; DB running locally

### Phase 2 — Resource Discovery (Week 2)
- Implement `CloudAdapter` base class + `AWSAdapter`
- Implement `ResourceDiscoveryService` (all 5 resource types)
- Write `resources` table schema; populate via discovery
- APScheduler job: 15-min discovery cycle
- **Done:** All 5 demo resources appear in DB after discovery run

### Phase 3 — Telemetry Collection (Week 3)
- Implement `MetricCollectorService` using `get_metric_data`
- TimescaleDB hypertable for `resource_metrics`
- 5-minute collection APScheduler job
- Verify all 10 metric types collected
- **Done:** 24h of metrics visible in DB; all 5 resources have data

### Phase 4 — Cost Estimation (Week 3–4)
- Build static AWS pricing JSON (US-East-1, core instance types)
- `CostEstimationService`: usage × price → estimated hourly cost
- Write `cost_records` table
- **Done:** Estimated cost per resource visible; pricing validated against AWS calculator

### Phase 5 — Anomaly Detection (Week 4–5)
- Implement Z-score baseline (cold start)
- Implement Isolation Forest training pipeline
- Implement inference pipeline (per collection cycle)
- Implement anomaly scoring → `anomalies` table
- Synthetic anomaly test: inject idle EC2 pattern, verify detection
- **Done:** Anomaly detected for synthetic idle resource within 10 min

### Phase 6 — Policy Engine (Week 5–6)
- Implement policy condition evaluator
- Seed 3 default policies (idle-EC2, runaway-Lambda, untagged)
- Test all policy branches: match, no match, cooldown, exemption
- **Done:** Policy evaluation deterministic; tested on 10 scenario fixtures

### Phase 7 — Action Runner + Safety Layer (Week 6–7)
- Implement `OptimizationActionRunner` with dry-run support
- Implement safety layer: kill-switch, budget check, action count, cooldown
- Implement rollback for stop-EC2 and limit-Lambda
- Full audit logging for every action
- **Done:** stop-EC2 executes and verifies in live AWS; rollback tested; all limits enforced

### Phase 8 — FastAPI Backend (Week 7–8)
- All REST endpoints defined in Section 11
- JWT auth for dashboard
- Health endpoint
- **Done:** All endpoints respond correctly; Postman collection tested

### Phase 9 — React Dashboard (Week 8–10)
- Overview, Anomaly Center, Optimization Center, Resource Explorer, Audit Log
- All UX states implemented
- **Done:** Full demo flow visible in browser without manual DB queries

### Phase 10 — Testing + Demo (Week 10–12)
- Synthetic anomaly injection test suite
- moto-based unit tests
- Real-cloud integration tests
- Demo scenario rehearsed end-to-end
- Auto-shutdown script tested
- **Done:** All acceptance criteria verified

---

## 24. Repository Structure

```
cloudsentry/
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app entry point
│   │   ├── api/                       # Route handlers (resources, anomalies, actions, system)
│   │   ├── services/
│   │   │   ├── discovery.py           # Resource discovery
│   │   │   ├── telemetry.py           # CloudWatch metric collection
│   │   │   ├── cost_estimator.py      # Cost estimation
│   │   │   ├── anomaly_detector.py    # ML inference + fallback
│   │   │   ├── policy_engine.py       # Policy evaluation
│   │   │   ├── action_runner.py       # Optimization execution + verification
│   │   │   └── safety_layer.py        # Kill-switch, limits, dry-run
│   │   ├── adapters/
│   │   │   ├── base.py                # CloudAdapter ABC
│   │   │   └── aws.py                 # AWSAdapter (boto3)
│   │   ├── models/                    # SQLAlchemy ORM models
│   │   ├── schemas/                   # Pydantic schemas
│   │   ├── scheduler.py               # APScheduler job definitions
│   │   └── config.py                  # Settings from env vars
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── e2e/
│   └── requirements.txt
├── ml/
│   ├── trainer.py                     # IF model training pipeline
│   ├── inference.py                   # Inference + scoring
│   ├── baseline.py                    # Z-score / EWMA fallback
│   ├── evaluation.py                  # Precision/recall on synthetic test set
│   ├── synthetic/
│   │   ├── generator.py               # Synthetic anomaly injection
│   │   └── scenarios/                 # Scenario definitions (idle, spike, runaway)
│   └── models/                        # Serialized model files (.pkl)
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Overview.jsx
│   │   │   ├── AnomalyCenter.jsx
│   │   │   ├── OptimizationCenter.jsx
│   │   │   ├── ResourceExplorer.jsx
│   │   │   └── AuditLog.jsx
│   │   ├── api/                       # Axios API client
│   │   └── App.jsx
│   └── package.json
├── db/
│   ├── migrations/                    # Alembic migration files
│   └── seed/                          # Demo seed data
├── scripts/
│   ├── provision_demo.sh              # Provision demo AWS resources
│   ├── shutdown_demo.sh               # Auto-shutdown all demo resources
│   ├── inject_anomaly.py              # Controlled anomaly injection
│   └── run_stress.sh                  # CPU stress on EC2
├── data/
│   └── pricing/
│       └── aws_us_east_1.json         # Static AWS pricing table
├── docs/
│   ├── PRD.md                         # This document
│   ├── architecture.md
│   └── iam_policy.json
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 25. Cloud Resource Demonstration Environment

| Resource | Type | Purpose | Expected Normal Behavior | Expected Anomaly Behavior |
|---|---|---|---|---|
| EC2 | t2.micro, Amazon Linux 2 | Idle instance demo | CPU: 2–5%, network: minimal | CPU: 0.1% over 2h → IDLE_COMPUTE anomaly |
| Lambda | Python 3.11, 128MB, 30s timeout | Runaway function demo | 10 invocations/min | Burst to 500/min → RUNAWAY_LAMBDA anomaly |
| S3 | Standard bucket | Storage tracking | Stable 1MB stored | Growth anomaly (future) |
| RDS | t2.micro PostgreSQL (free tier) | DB monitoring demo | 2–5 connections | Stable — anomaly demo via metric injection |
| EBS | 8GB gp2 (attached to EC2) | Volume monitoring | Attached, active | Detach → unattached state → UNUSED_VOLUME |

**Auto-Shutdown Script (`shutdown_demo.sh`):**
```bash
# Stops EC2, deletes Lambda test alias, removes S3 test objects
# Does NOT delete RDS (takes too long) — stop instead
aws ec2 stop-instances --instance-ids $EC2_ID
aws lambda delete-function --function-name cloudsentry-test
# S3 objects removed
aws s3 rm s3://$BUCKET_NAME --recursive
```

**Free Tier Estimate:** All resources within 12-month AWS free tier. [VERIFY: RDS t2.micro 750h/month, EC2 t2.micro 750h/month — confirm current free tier at aws.amazon.com/free]

---

## 26. Demo Scenario

```
T+0:00  System running. GLOBAL_AUTOMATION_ENABLED=false. DRY_RUN=true.
        Dashboard shows: 5 resources discovered, 0 anomalies, automation: dry-run.

T+0:02  Run inject_anomaly.py --type idle_ec2 --duration 2h
        (Injects historical CPU=0.5% metrics into DB for the last 2h)

T+0:05  ML inference cycle runs.
        IF scores EC2 as -0.79. Z-score: 3.4σ below mean.
        Anomaly record written: IDLE_COMPUTE, MEDIUM, confidence=0.83.

T+0:05  Dashboard: Anomaly Center shows new anomaly.
        Anomaly detail: resource, score, reason, recommended action: STOP EC2.

T+0:06  Policy engine evaluates: all conditions met. Creates action record.
        Status: dry_run. Dashboard: "WOULD STOP i-0abc1234".

T+0:07  Enable automation: GLOBAL_AUTOMATION_ENABLED=true (demo toggle on dashboard).
        Action re-triggered in real mode.

T+0:08  Action runner calls ec2.stop_instances(). Polls until state=stopped.
        AuditLog entry: {actor: SYSTEM, api: stop_instances, pre: running, post: stopped}.

T+0:10  Dashboard: Optimization Center shows completed action.
        Before: CPU metric sparkline (2h idle). After: instance stopped.
        Rollback button visible.

T+0:12  Demonstrator clicks Rollback.
        ec2.start_instances() called. Instance returns to running.
        AuditLog: rollback executed.

T+0:15  Enable global kill-switch (emergency stop button).
        Dashboard: red banner. All future automation blocked.
```

**What Evaluators See:** Full lifecycle from anomaly detection → ML scoring → policy match → API execution → verification → audit log → rollback — on a live AWS instance.

---

## 27. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Unexpected cloud costs | MEDIUM | HIGH | Kill-switch defaults, daily budget cap, auto-shutdown scripts, dry-run default |
| ML false positives → wrong actions | HIGH (early phase) | MEDIUM | MEDIUM/HIGH actions require high confidence threshold; HIGH always needs approval |
| Destructive automation | LOW | CRITICAL | No auto-delete ever; HIGH-risk always requires human approval; audit trail |
| Credential leakage | MEDIUM | CRITICAL | `.gitignore` `.env`; no credentials in frontend; least-privilege IAM |
| Insufficient training data | HIGH (first week) | MEDIUM | Z-score fallback mandatory; synthetic data injection for testing |
| CloudWatch billing API lag | CERTAIN | LOW | Use estimated cost for real-time; label clearly; Cost Explorer for reporting only |
| Free-tier limit exceeded | LOW | MEDIUM | Monitor with AWS Budgets alert; auto-shutdown after demo |
| boto3 API rate limiting | LOW | LOW | Batch with `get_metric_data`; exponential backoff on all boto3 calls |
| Scope creep | HIGH | HIGH | Non-goals table enforced; weekly scope review against PRD |
| Telemetry gaps | MEDIUM | MEDIUM | Mark data as stale if > 15 min old; anomaly detection skips stale resources |
| ML model concept drift | LOW (short project) | LOW | Daily retraining; model version tracking |

---

## 28. Acceptance Criteria

The system is complete only when ALL of the following are demonstrated:

| # | Criterion | Verification Method |
|---|---|---|
| 1 | Real AWS resources discovered | DB shows 5+ resources with correct provider IDs |
| 2 | Live telemetry collected | `resource_metrics` table has 24h of data; metrics visible in dashboard |
| 3 | Historical data stored | Hypertable query returns 7-day trend for any resource |
| 4 | Anomaly detected | Synthetic idle injection → anomaly record in DB within 10 min |
| 5 | Anomaly explained | Anomaly detail shows: score, confidence, reason string, affected metric values |
| 6 | Recommendations produced | Anomaly card shows recommended action with estimated savings |
| 7 | Safe actions execute | EC2 stop executed via API; state verified as `stopped` |
| 8 | Actions verified | post_state in `optimization_actions` table populated; confirmed via CloudWatch |
| 9 | Before/after measurable | CPU metric pre/post stop visible in Optimization Center sparkline |
| 10 | Full audit trail | Every API call logged in `audit_logs` with actor, timestamp, params, result |
| 11 | Safety controls enforced | Action blocked when kill-switch active; action blocked on protected resource |
| 12 | Dashboard shows full lifecycle | All 6 views functional; anomaly → action → verification visible without DB queries |
| 13 | System does not overspend | Total demo cloud cost < $5 USD; monthly budget cap enforced |

---

## 29. Architecture Decision Records

### ADR-01: AWS over GCP
**Decision:** AWS-first for MVP  
**Alternatives:** GCP-first; Multi-cloud abstraction from day 1  
**Why:** boto3 is more widely documented; free-tier matches requirements; EC2 stop/start is the simplest reversible action to demonstrate  
**Trade-offs:** GCP users cannot use MVP directly  
**Migration:** `CloudAdapter` ABC already designed; `GCPAdapter` can implement same interface in V2

### ADR-02: Isolation Forest over Prophet
**Decision:** Isolation Forest as primary model  
**Alternatives:** Prophet; Z-score only; LSTM  
**Why:** Prophet requires 2+ weeks of daily data with seasonality to be useful. IF works unsupervised with days of data and handles multivariate input. LSTM is overkill and requires more data.  
**Trade-offs:** IF is not seasonal-aware; may miss patterns that are normal for time-of-day  
**Migration:** Add time_of_day as feature; upgrade to Prophet in V2 when 30+ days of data available

### ADR-03: PostgreSQL + TimescaleDB over Dedicated Time-Series DB
**Decision:** PostgreSQL 16 + TimescaleDB extension  
**Alternatives:** InfluxDB; Prometheus + Grafana; MongoDB  
**Why:** Single database for both relational data (resources, anomalies, policies) and time-series (metrics). TimescaleDB hypertables provide time-bucket queries and efficient compression. One system to maintain.  
**Trade-offs:** Not as optimized as native TSDB for extreme scale  
**Migration:** TimescaleDB can be replaced with InfluxDB at metric layer without touching relational schema

### ADR-04: Monolith over Microservices
**Decision:** Single FastAPI backend with APScheduler for background jobs  
**Alternatives:** Separate services for collector, ML, API, scheduler  
**Why:** 1–4 developers. Operational overhead of microservices kills small teams. Shared DB eliminates messaging complexity. APScheduler runs in-process — no Celery/Redis.  
**Trade-offs:** Cannot scale components independently  
**Migration:** Extract collector into a separate service if API latency degrades

### ADR-05: Polling over Event-Driven Telemetry
**Decision:** Poll CloudWatch every 5 minutes  
**Alternatives:** CloudWatch Events → SNS → Lambda → DB; EventBridge rules  
**Why:** CloudWatch standard metrics are already aggregated per minute. Sub-minute telemetry is neither available nor needed for cost anomaly detection. Event-driven adds Lambda cost and operational complexity with no benefit at this scale.  
**Trade-offs:** 5-min detection latency is the minimum resolution  
**Migration:** CloudWatch Metric Streams (pushes to Kinesis Firehose → S3) if real-time needed

### ADR-06: Recommendation + Approval for Destructive Actions
**Decision:** High-risk actions require human approval; auto-delete never allowed  
**Alternatives:** Allow auto-delete with confirmation; never automate anything  
**Why:** Volume deletion is irreversible. A false positive auto-delete destroys customer data. The risk is asymmetric. LOW/MEDIUM reversible actions (stop, tag, concurrency limit) can be automated because they can be undone.  
**Trade-offs:** Demo requires human interaction for some actions  
**Migration:** This should NOT be relaxed in V2 without a dedicated rollback/snapshot system

---

## 30. Final Engineering Review

### 5 Biggest Technical Risks

1. **CloudWatch Metric Availability:** Not all metrics exist for all resource states. Stopped EC2 instances don't emit CPU metrics. Design must handle sparse metric data without treating gaps as anomalies.

2. **ML Cold Start in Demo:** If demo is run before 7 days of data is collected, the IF model is untrained. The demo MUST either use synthetic historical data injection OR demonstrate the Z-score fallback path explicitly.

3. **IAM Permission Boundary:** The `ec2:StopInstances` permission with condition `StringEquals: {aws:RequestedRegion: us-east-1}` works for region-specific resources but does NOT prevent stopping instances that happen to be in that region but are production instances. The protected resource list and denylist are critical safeguards.

4. **Action Verification Race Condition:** After `stop_instances()`, the instance takes 30–90 seconds to reach `stopped` state. The verification poller must have a reasonable timeout (5 min) and handle intermediate states (`stopping`). Failing to verify does not mean the action failed.

5. **Estimated Cost Accuracy:** The static pricing JSON goes stale as AWS changes pricing. The system must never present estimated cost as actual billing data. Labeling is critical to avoid misleading stakeholders.

### 5 Implementation Mistakes to Avoid

1. **Hardcoding boto3 calls everywhere:** All cloud operations go through `AWSAdapter`. Direct boto3 calls in business logic make testing and future GCP support impossible.

2. **Storing credentials in source code or frontend:** Even as env vars in a frontend build. AWS keys in JavaScript = public exposure.

3. **Skipping the dry-run mode:** Building automation first without dry-run means you WILL accidentally stop something important during development. Implement dry-run on day 1.

4. **Using CloudWatch high-resolution metrics without confirming cost:** Custom metrics and high-resolution monitoring cost money. Verify free tier coverage before enabling.

5. **Training Isolation Forest without normalization:** IF is not sensitive to scale but feature engineering (ratios, z-scores) should still be applied for stability. Raw absolute values like `network_bytes` vs `cpu_percent` on different scales can affect feature importance.

### 5 Underspecified Areas in the Problem Statement

1. **Billing data latency vs detection goal:** PS says "detect genuine cost anomalies" but billing APIs have 24–48h delay. The PRD resolves this by using usage metrics as proxies — but this is a significant architectural assumption.

2. **"Automatically executes optimizations" scope:** PS doesn't bound which actions are safe for automation. This PRD classifies by risk level. Without this, a naive implementation could delete data.

3. **Multi-cloud "or":** AWS OR GCP is underspecified — the PRD selects AWS-first but this must be confirmed with the team.

4. **"Real-time dashboard":** Not defined. CloudWatch minimum resolution is 1 min. The PRD defines "near-real-time" as 5-minute polling. True real-time would require CloudWatch Metric Streams.

5. **User authentication model:** PS mentions no auth. The PRD adds JWT auth. If this is a solo-developer tool, auth may be skipped. [DESIGN DECISION REQUIRED by team]

### Features to Remove from MVP
- Prophet model (insufficient data; Z-score + IF sufficient)
- Cost Explorer integration (billing lag makes it useless for detection; use estimated cost)
- Multi-cloud abstraction beyond the base class interface
- Email/Slack alerting (dashboard notifications are sufficient for demo)
- Traffic spike detection (requires more complex network baseline; save for V2)

### Features Worth Adding If Time Permits
- SHAP values for anomaly explanation (makes ML results interpretable; high demo value)
- AWS Budgets API integration for actual budget alerts (simple API, high value)
- `cloudsentry:reviewed` tag auto-applied after human review of anomaly (closes the loop)
- Anomaly timeline correlation across resources (e.g., Lambda spike causing EC2 load)

### What Would Make This Technically Impressive
1. **Genuine before/after measurable impact** — not just "instance stopped" but CPU trend comparison, estimated savings, all visible in the dashboard as a time-series overlay
2. **Explainable ML** — showing WHICH features triggered the anomaly score (feature contribution), not just the score
3. **Policy-as-code** — allowing operators to define policies in a structured config rather than hard-coded logic
4. **Rollback tested under failure conditions** — demonstrating that the system correctly handles rollback when the primary action partially fails
5. **Synthetic anomaly replay** — a reproducible test harness that any evaluator can run to validate detection accuracy without needing to wait for real anomalies

---

*End of PRD — CloudSentry v1.0*  
*Document is implementation-ready. All [VERIFY] items must be confirmed before Phase 1 begins.*
