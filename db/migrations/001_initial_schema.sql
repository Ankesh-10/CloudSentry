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
    metadata JSONB -- instance type, runtime, etc.
);

-- ResourceMetric
CREATE TABLE resource_metrics (
    id BIGSERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL,
    resource_id UUID NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    metric_name VARCHAR(50) NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit VARCHAR(30)
);

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

-- SystemConfig
CREATE TABLE system_config (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
