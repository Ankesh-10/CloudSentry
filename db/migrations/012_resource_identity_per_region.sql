-- 012: resource identity includes the region.
-- Idempotent. Run after 001-011.
--
-- Lambda function names and RDS instance identifiers are only unique within a
-- region. With AWS_REGIONS listing more than one region, (resource_type,
-- provider_id) let a function named "api" in eu-west-1 collide with "api" in
-- us-east-1: discovery kept one and silently dropped the other, so it was never
-- monitored or acted on. EC2/EBS ids and S3 bucket names are globally unique,
-- so adding the region changes nothing for them.

-- region becomes part of the key, so it can no longer be NULL. Discovery has
-- always set it; backfill any legacy row from its account.
UPDATE resources r
SET region = COALESCE(a.region, 'unknown')
FROM cloud_accounts a
WHERE r.region IS NULL AND r.account_id = a.id;

UPDATE resources SET region = 'unknown' WHERE region IS NULL;

ALTER TABLE resources ALTER COLUMN region SET NOT NULL;

ALTER TABLE resources DROP CONSTRAINT IF EXISTS resources_type_provider_id_key;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'resources_type_region_provider_id_key') THEN
        ALTER TABLE resources
            ADD CONSTRAINT resources_type_region_provider_id_key UNIQUE (resource_type, region, provider_id);
    END IF;
END $$;

INSERT INTO schema_migrations (version) VALUES (12) ON CONFLICT (version) DO NOTHING;
