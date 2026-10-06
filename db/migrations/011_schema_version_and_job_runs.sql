-- 011: schema version tracking and persisted job status.
-- Idempotent. Run after 001-010.

-- ---------------------------------------------------------------------------
-- 1. schema_migrations: the API refuses to start unless MAX(version) is at
--    least the newest file in db/migrations. scripts/apply_migrations.py
--    records each file it applies; migrations from 011 on also record
--    themselves so applying them by hand in the SQL editor works too.
--    Reaching 011 implies 001-010 were applied, so backfill those.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_migrations (version)
SELECT v FROM generate_series(1, 11) AS v
ON CONFLICT (version) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. job_runs: last outcome of each scheduled job, written by the leader so
--    /system/health is meaningful on every replica and survives restarts.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_runs (
    job                  VARCHAR(64) PRIMARY KEY,
    last_success         TIMESTAMPTZ,
    last_failure         TIMESTAMPTZ,
    last_error           TEXT,
    consecutive_failures INT NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Service role only (the API reads these and decides what to expose).
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON schema_migrations, job_runs FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON schema_migrations, job_runs FROM authenticated;
    END IF;
END $$;
