-- 009: vocabulary constraints, explicit grants, role-gated reads.
-- Idempotent: safe to run more than once. Run after 001-008.
-- Fails loudly if existing rows violate a constraint: fix the reported rows,
-- then re-run.

-- ---------------------------------------------------------------------------
-- 1. Vocabulary checks. Added NOT VALID (cheap, enforced for new rows), then
--    validated against existing rows.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION pg_temp.add_check(tbl text, cname text, expr text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    bad bigint;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = cname) THEN
        EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I CHECK (%s) NOT VALID', tbl, cname, expr);
    END IF;
    EXECUTE format('SELECT COUNT(*) FROM %I WHERE NOT (%s)', tbl, expr) INTO bad;
    IF bad > 0 THEN
        RAISE EXCEPTION '% has % row(s) violating %: fix them, then re-run 009', tbl, bad, cname;
    END IF;
    EXECUTE format('ALTER TABLE %I VALIDATE CONSTRAINT %I', tbl, cname);
END $$;

SELECT pg_temp.add_check('resources', 'resources_resource_type_check',
    $c$resource_type IN ('ec2','lambda','s3','rds','ebs')$c$);
-- start_ec2 / restore_lambda_concurrency are written by rollbacks (action_runner.REVERSIBLE).
SELECT pg_temp.add_check('optimization_actions', 'optimization_actions_action_type_check',
    $c$action_type IN ('stop_ec2','start_ec2','limit_lambda','restore_lambda_concurrency','apply_tags')$c$);
SELECT pg_temp.add_check('optimization_actions', 'optimization_actions_risk_level_check',
    $c$risk_level IS NULL OR risk_level IN ('LOW','MEDIUM','HIGH')$c$);
SELECT pg_temp.add_check('optimization_actions', 'optimization_actions_savings_check',
    $c$estimated_savings_usd IS NULL OR estimated_savings_usd >= 0$c$);
SELECT pg_temp.add_check('anomalies', 'anomalies_severity_check',
    $c$severity IS NULL OR severity IN ('LOW','MEDIUM','HIGH')$c$);
SELECT pg_temp.add_check('anomalies', 'anomalies_confidence_check',
    $c$confidence IS NULL OR (confidence >= 0 AND confidence <= 1)$c$);
SELECT pg_temp.add_check('policies', 'policies_resource_type_check',
    $c$resource_type IN ('ec2','lambda','s3','rds','ebs','*')$c$);
SELECT pg_temp.add_check('policies', 'policies_action_type_check',
    $c$action_type IN ('stop_ec2','start_ec2','limit_lambda','apply_tags')$c$);
SELECT pg_temp.add_check('policies', 'policies_risk_level_check',
    $c$risk_level IN ('LOW','MEDIUM','HIGH')$c$);
SELECT pg_temp.add_check('cost_records', 'cost_records_nonnegative_check',
    $c$(estimated_cost_usd IS NULL OR estimated_cost_usd >= 0) AND (actual_cost_usd IS NULL OR actual_cost_usd >= 0)$c$);

-- The status checks 007 added NOT VALID: validate them for historical rows too.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM optimization_actions WHERE status NOT IN
               ('pending','pending_approval','approved','rejected','executing','completed','failed','rolled_back')) THEN
        RAISE EXCEPTION 'optimization_actions has rows with an unknown status: fix them, then re-run 009';
    END IF;
    IF EXISTS (SELECT 1 FROM anomalies WHERE status NOT IN ('active','resolved','false_positive')) THEN
        RAISE EXCEPTION 'anomalies has rows with an unknown status: fix them, then re-run 009';
    END IF;
END $$;
ALTER TABLE optimization_actions VALIDATE CONSTRAINT optimization_actions_status_check;
ALTER TABLE anomalies VALIDATE CONSTRAINT anomalies_status_check;

-- Every resource belongs to a cloud account (discovery always sets it).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM resources WHERE account_id IS NULL) THEN
        RAISE NOTICE 'resources.account_id has NULLs; left nullable. Backfill, then re-run 009.';
    ELSE
        ALTER TABLE resources ALTER COLUMN account_id SET NOT NULL;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2. Explicit grants. Supabase's defaults grant ALL on public tables to anon
--    and authenticated and rely on RLS alone; make reads-only explicit so a
--    missing or dropped policy can never re-open writes. The backend uses the
--    service role and is unaffected.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['cloud_accounts','resources','resource_metrics','cost_records','anomalies',
                             'optimization_actions','audit_logs','policies','system_config'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            EXECUTE format('REVOKE ALL ON %I FROM anon', t);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            EXECUTE format('REVOKE ALL ON %I FROM authenticated', t);
            EXECUTE format('GRANT SELECT ON %I TO authenticated', t);
        END IF;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 3. Role-gated reads (direct PostgREST and Realtime). Mirrors the API's
--    viewer rule: app_metadata.cloudsentry_role in viewer/operator/admin, and
--    never an anonymous session. VIEWER_USER_IDS/OPERATOR_USER_IDS are
--    API-only; users who read Supabase directly need the app_metadata role.
--    Skipped on plain Postgres (no auth.jwt()).
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t text;
BEGIN
    IF to_regprocedure('auth.jwt()') IS NULL THEN
        RAISE NOTICE 'auth.jwt() not found (not Supabase); role-gated read policies skipped';
        RETURN;
    END IF;

    EXECUTE $f$
        CREATE OR REPLACE FUNCTION public.cloudsentry_can_read() RETURNS boolean
        LANGUAGE sql STABLE SECURITY INVOKER AS $body$
            SELECT COALESCE(auth.jwt() -> 'app_metadata' ->> 'cloudsentry_role', '') IN ('viewer','operator','admin')
               AND COALESCE((auth.jwt() ->> 'is_anonymous')::boolean, false) = false
        $body$
    $f$;

    FOREACH t IN ARRAY ARRAY['cloud_accounts','resources','resource_metrics','cost_records','anomalies',
                             'optimization_actions','audit_logs','policies','system_config'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'select_' || t, t);
        EXECUTE format('CREATE POLICY %I ON %I FOR SELECT TO authenticated USING (public.cloudsentry_can_read())',
                       'select_' || t, t);
    END LOOP;
END $$;
