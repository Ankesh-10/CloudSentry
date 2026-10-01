-- Tighten authenticated writes (Supabase role only; no-op on vanilla Postgres).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE UPDATE ON anomalies FROM authenticated;
        REVOKE UPDATE ON optimization_actions FROM authenticated;
        REVOKE UPDATE ON policies FROM authenticated;
        REVOKE UPDATE ON system_config FROM authenticated;
        GRANT UPDATE (status, resolved_at) ON anomalies TO authenticated;
        GRANT UPDATE (status, approved_by, approved_at) ON optimization_actions TO authenticated;
        GRANT UPDATE (value, updated_at) ON system_config TO authenticated;
    END IF;
END $$;

DROP POLICY IF EXISTS update_policies ON policies;

-- Realtime (no-op on vanilla Postgres without the publication)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
        BEGIN
            ALTER PUBLICATION supabase_realtime ADD TABLE anomalies;
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
        BEGIN
            ALTER PUBLICATION supabase_realtime ADD TABLE optimization_actions;
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END IF;
END $$;

ALTER TABLE anomalies REPLICA IDENTITY FULL;
ALTER TABLE optimization_actions REPLICA IDENTITY FULL;

-- Replace forbidden delete-volume seed with tag remediation
DELETE FROM policies WHERE action_type = 'delete_ebs_volume';

INSERT INTO policies (name, enabled, resource_type, anomaly_type, conditions, action_type, risk_level, requires_approval, priority)
SELECT
    'Apply tags to untagged resources',
    true,
    '*',
    'untagged_resource',
    '{"AND": [{"field": "resource.protected", "op": "eq", "value": false}]}',
    'apply_tags',
    'LOW',
    false,
    200
WHERE NOT EXISTS (
    SELECT 1 FROM policies WHERE name = 'Apply tags to untagged resources'
);
