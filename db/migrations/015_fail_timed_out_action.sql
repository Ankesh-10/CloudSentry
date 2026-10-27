-- 015: atomic failure of an action whose verification timed out.
-- Idempotent. Run after 001-014, and apply before deploying the API release
-- that calls it (the verify job will otherwise fail every timeout).
--
-- ActionRunner.verify_pending used to fail a timed-out action with a guarded
-- UPDATE and then, whether or not that UPDATE matched a row, release the
-- rollback link and write an `action_failed` audit row. When another
-- replica's verify pass had already completed the action, that left a
-- failure row contradicting its `action_verified` row (and a spurious
-- alert). This function is the timeout twin of complete_verified_action
-- (014): one transaction, and nothing at all when the action is no longer
-- `executing`.

CREATE OR REPLACE FUNCTION public.fail_timed_out_action(
    p_action_id uuid,
    p_observed  jsonb,
    p_message   text
) RETURNS boolean
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_resource_id uuid;
    v_action_type text;
BEGIN
    UPDATE optimization_actions
       SET status = 'failed',
           post_state = jsonb_build_object('state', p_observed, 'verified', false, 'timeout', true)
     WHERE id = p_action_id AND status = 'executing'
    RETURNING resource_id, action_type
         INTO v_resource_id, v_action_type;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    -- A failed rollback must not block a retry: unlink it from the original
    -- (still `completed`). A no-op for a forward action.
    UPDATE optimization_actions SET rollback_action_id = NULL
     WHERE rollback_action_id = p_action_id AND status = 'completed';

    INSERT INTO audit_logs (event_type, actor, resource_id, action_id, aws_api_call, response_status, message)
    VALUES ('action_failed', 'SYSTEM', v_resource_id, p_action_id, v_action_type, 'failed', p_message);

    RETURN true;
END $$;

-- Service role only. Functions are executable by PUBLIC by default, and
-- PostgREST exposes every function the caller may execute.
REVOKE ALL ON FUNCTION public.fail_timed_out_action(uuid, jsonb, text) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.fail_timed_out_action(uuid, jsonb, text) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON FUNCTION public.fail_timed_out_action(uuid, jsonb, text) FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION public.fail_timed_out_action(uuid, jsonb, text) TO service_role;
    END IF;
END $$;

INSERT INTO schema_migrations (version) VALUES (15) ON CONFLICT (version) DO NOTHING;

-- Make PostgREST pick up the new function without a restart.
NOTIFY pgrst, 'reload schema';
