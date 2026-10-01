-- 007: data-integrity keys, execution bookkeeping, and API-only writes.
-- Idempotent: safe to run more than once. Run after 001-006.

-- ---------------------------------------------------------------------------
-- 1. Resource identity: (resource_type, provider_id).
--    provider_id alone collides across types (a Lambda and an S3 bucket may
--    share a name), which made discovery overwrite one row with the other.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM resources GROUP BY resource_type, provider_id HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'resources has duplicate (resource_type, provider_id) rows; merge them before applying 007';
    END IF;
END $$;

ALTER TABLE resources DROP CONSTRAINT IF EXISTS resources_provider_id_key;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'resources_type_provider_id_key') THEN
        ALTER TABLE resources
            ADD CONSTRAINT resources_type_provider_id_key UNIQUE (resource_type, provider_id);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Execution bookkeeping.
-- ---------------------------------------------------------------------------
ALTER TABLE optimization_actions ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

-- Status vocabularies (NOT VALID: enforced for new/updated rows only).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'optimization_actions_status_check') THEN
        ALTER TABLE optimization_actions ADD CONSTRAINT optimization_actions_status_check
            CHECK (status IN ('pending','pending_approval','approved','rejected','executing','completed','failed','rolled_back'))
            NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'anomalies_status_check') THEN
        ALTER TABLE anomalies ADD CONSTRAINT anomalies_status_check
            CHECK (status IN ('active','resolved','false_positive'))
            NOT VALID;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. At most one active anomaly per (resource, type). Earlier detector
--    versions inserted a new row every cycle; keep the newest active one.
-- ---------------------------------------------------------------------------
UPDATE anomalies SET status = 'resolved', resolved_at = NOW()
WHERE status = 'active'
  AND id NOT IN (
      SELECT DISTINCT ON (resource_id, anomaly_type) id
      FROM anomalies
      WHERE status = 'active'
      ORDER BY resource_id, anomaly_type, detected_at DESC
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_anomalies_one_active
    ON anomalies (resource_id, anomaly_type) WHERE status = 'active';

-- At most one in-flight action per (resource, action_type): a DB-level guard
-- behind the SafetyLayer duplicate check (which is racy across replicas).
UPDATE optimization_actions
SET status = 'failed',
    post_state = COALESCE(post_state, '{}'::jsonb) || '{"result": "Superseded duplicate (migration 007)"}'::jsonb
WHERE status IN ('pending','pending_approval','approved','executing')
  AND id NOT IN (
      SELECT DISTINCT ON (resource_id, action_type) id
      FROM optimization_actions
      WHERE status IN ('pending','pending_approval','approved','executing')
      ORDER BY resource_id, action_type, created_at DESC
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_actions_one_in_flight
    ON optimization_actions (resource_id, action_type)
    WHERE status IN ('pending','pending_approval','approved','executing');

-- ---------------------------------------------------------------------------
-- 4. Cost records: one estimate per resource per hour (the estimator writes
--    recorded_at truncated to the hour and upserts on this key).
-- ---------------------------------------------------------------------------
DELETE FROM cost_records a
USING cost_records b
WHERE a.resource_id = b.resource_id
  AND a.recorded_at = b.recorded_at
  AND a.id > b.id;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'cost_records_resource_hour_key') THEN
        ALTER TABLE cost_records
            ADD CONSTRAINT cost_records_resource_hour_key UNIQUE (resource_id, recorded_at);
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 5. Query-path indexes.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_actions_status ON optimization_actions (status);
CREATE INDEX IF NOT EXISTS idx_actions_anomaly ON optimization_actions (anomaly_id);
CREATE INDEX IF NOT EXISTS idx_actions_resource_executed ON optimization_actions (resource_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomalies_status ON anomalies (status);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cost_recorded ON cost_records (recorded_at);

-- ---------------------------------------------------------------------------
-- 6. Writes go through the API only.
--    The API stamps the actor from the verified JWT, enforces status
--    transitions and the operator role, and keeps the in-process kill-switch in
--    sync. Direct PostgREST writes with a user JWT bypassed all of that
--    (006 still allowed authenticated users to flip GLOBAL_AUTOMATION_ENABLED).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE INSERT, UPDATE, DELETE ON anomalies FROM authenticated;
        REVOKE INSERT, UPDATE, DELETE ON optimization_actions FROM authenticated;
        REVOKE INSERT, UPDATE, DELETE ON policies FROM authenticated;
        REVOKE INSERT, UPDATE, DELETE ON system_config FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE INSERT, UPDATE, DELETE ON anomalies, optimization_actions, policies, system_config FROM anon;
    END IF;
END $$;

DROP POLICY IF EXISTS update_anomalies ON anomalies;
DROP POLICY IF EXISTS update_optimization_actions ON optimization_actions;
DROP POLICY IF EXISTS update_policies ON policies;
DROP POLICY IF EXISTS update_system_config ON system_config;
