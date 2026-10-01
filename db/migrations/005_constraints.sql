-- Idempotent: safe to run more than once, and safe on a database that already
-- holds duplicate CloudWatch samples written by earlier telemetry versions.

-- Prevent duplicate CloudWatch samples that break pandas pivot().
-- Existing duplicates must be removed first or ADD CONSTRAINT fails.
DELETE FROM resource_metrics a
USING resource_metrics b
WHERE a.resource_id = b.resource_id
  AND a.time = b.time
  AND a.metric_name = b.metric_name
  AND a.id > b.id;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'resource_metrics_unique_point') THEN
        ALTER TABLE resource_metrics
            ADD CONSTRAINT resource_metrics_unique_point UNIQUE (resource_id, time, metric_name);
    END IF;
END $$;

-- Resource identity is (resource_type, provider_id): see 007_integrity_and_rls.sql.
-- An earlier revision of this file added UNIQUE (provider_id); 007 replaces it.
