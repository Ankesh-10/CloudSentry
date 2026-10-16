-- 013: review idle or nearly full RDS databases.
-- Idempotent. Run after 001-012.
--
-- RDS metrics (DatabaseConnections, FreeStorageSpace) were collected but no
-- rule looked at them. The detector now raises idle_database (no connections
-- for the whole ML_IDLE_WINDOW_HOURS) and low_storage (free space under
-- RDS_LOW_STORAGE_PCT of the allocated size). RDS state is never changed
-- automatically (the safety layer allows only tags and recommendations), so
-- both policies record a recommendation that waits for a human.

INSERT INTO policies (name, enabled, resource_type, anomaly_type, conditions, action_type, risk_level, requires_approval, priority)
SELECT
    'Review idle RDS databases',
    true,
    'rds',
    'idle_database',
    '{"AND": [{"field": "resource.state", "op": "eq", "value": "available"}]}',
    'recommend_review',
    'LOW',
    true,
    100
WHERE NOT EXISTS (
    SELECT 1 FROM policies WHERE name = 'Review idle RDS databases'
);

INSERT INTO policies (name, enabled, resource_type, anomaly_type, conditions, action_type, risk_level, requires_approval, priority)
SELECT
    'Review RDS databases running out of storage',
    true,
    'rds',
    'low_storage',
    '{"AND": [{"field": "resource.state", "op": "eq", "value": "available"}]}',
    'recommend_review',
    'LOW',
    true,
    100
WHERE NOT EXISTS (
    SELECT 1 FROM policies WHERE name = 'Review RDS databases running out of storage'
);

INSERT INTO schema_migrations (version) VALUES (13) ON CONFLICT (version) DO NOTHING;
