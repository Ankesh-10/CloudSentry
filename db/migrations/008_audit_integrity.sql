-- 008: append-only audit trail and request correlation.
-- Idempotent: safe to run more than once. Run after 001-007.
-- The API (>= this release) writes request_id/client_ip; apply this migration
-- before deploying it, or every audit write -- and so every audited change --
-- will fail.

-- ---------------------------------------------------------------------------
-- 1. Request correlation: which HTTP request (X-Request-ID) and client IP
--    produced each row. NULL for scheduler-initiated events.
-- ---------------------------------------------------------------------------
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_id TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS client_ip TEXT;
CREATE INDEX IF NOT EXISTS idx_audit_request_id ON audit_logs (request_id) WHERE request_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. Append-only. The backend holds the service-role key, which bypasses RLS,
--    so RLS alone cannot protect history from a leaked key or a buggy job.
--    Triggers apply to every role, service_role included:
--      * UPDATE and TRUNCATE are always refused;
--      * DELETE is refused for rows younger than 90 days (the retention job
--        only removes rows older than AUDIT_RETENTION_DAYS, minimum 90).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit_logs_block_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'audit_logs is append-only: UPDATE is not allowed';
    ELSIF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'audit_logs is append-only: TRUNCATE is not allowed';
    ELSIF TG_OP = 'DELETE' AND OLD.created_at > NOW() - INTERVAL '90 days' THEN
        RAISE EXCEPTION 'audit_logs rows younger than 90 days cannot be deleted';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS trg_audit_logs_no_update ON audit_logs;
CREATE TRIGGER trg_audit_logs_no_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation();

DROP TRIGGER IF EXISTS trg_audit_logs_no_recent_delete ON audit_logs;
CREATE TRIGGER trg_audit_logs_no_recent_delete
    BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_block_mutation();

DROP TRIGGER IF EXISTS trg_audit_logs_no_truncate ON audit_logs;
CREATE TRIGGER trg_audit_logs_no_truncate
    BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_block_mutation();

-- Rows are written by the API (service role) only; end users never write.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit_logs FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit_logs FROM anon;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 3. Indexes for the retention job (older-than scans on history tables).
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_actions_created ON optimization_actions (created_at);
CREATE INDEX IF NOT EXISTS idx_anomalies_detected ON anomalies (detected_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs (action_id) WHERE action_id IS NOT NULL;
