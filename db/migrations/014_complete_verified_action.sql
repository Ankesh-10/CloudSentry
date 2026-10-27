-- 014: atomic completion of a verified action.
-- Idempotent. Run after 001-013, and apply before deploying the API release
-- that calls it (the verify job will otherwise fail every verification).
--
-- ActionRunner.verify_pending used to record a verified action with four
-- separate PostgREST writes; a crash between them left the action
-- `completed` with its anomaly still open, the resource state stale, no
-- audit row, or a rolled-back original still `completed` -- and the verify
-- job never revisits a completed action. One function call is one
-- transaction, so all of it commits or none of it does.
--
-- Python decides what to write (target state, forward vs. rollback); this
-- function only applies it. Returns false, writing nothing, when the action
-- is no longer `executing` (another replica or the stale-claim sweep won).

CREATE OR REPLACE FUNCTION public.complete_verified_action(
    p_action_id      uuid,
    p_observed       jsonb,
    p_resource_state text,
    p_is_rollback    boolean,
    p_message        text
) RETURNS boolean
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
    v_resource_id uuid;
    v_anomaly_id  uuid;
    v_action_type text;
BEGIN
    UPDATE optimization_actions
       SET status = 'completed',
           verified_at = NOW(),
           post_state = jsonb_build_object('state', p_observed, 'verified', true)
     WHERE id = p_action_id AND status = 'executing'
    RETURNING resource_id, anomaly_id, action_type
         INTO v_resource_id, v_anomaly_id, v_action_type;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    IF p_resource_state IS NOT NULL AND v_resource_id IS NOT NULL THEN
        UPDATE resources SET state = p_resource_state WHERE id = v_resource_id;
    END IF;

    INSERT INTO audit_logs (event_type, actor, resource_id, action_id, aws_api_call, response_status, message)
    VALUES ('action_verified', 'SYSTEM', v_resource_id, p_action_id, v_action_type, 'completed', p_message);

    IF p_is_rollback THEN
        -- A completed rollback marks the forward action it undid.
        UPDATE optimization_actions SET status = 'rolled_back' WHERE rollback_action_id = p_action_id;
    ELSIF v_anomaly_id IS NOT NULL THEN
        UPDATE anomalies SET status = 'resolved', resolved_at = NOW()
         WHERE id = v_anomaly_id AND status = 'active';
    END IF;

    RETURN true;
END $$;

-- Service role only. Functions are executable by PUBLIC by default, and
-- PostgREST exposes every function the caller may execute.
REVOKE ALL ON FUNCTION public.complete_verified_action(uuid, jsonb, text, boolean, text) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.complete_verified_action(uuid, jsonb, text, boolean, text) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON FUNCTION public.complete_verified_action(uuid, jsonb, text, boolean, text) FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION public.complete_verified_action(uuid, jsonb, text, boolean, text) TO service_role;
    END IF;
END $$;

INSERT INTO schema_migrations (version) VALUES (14) ON CONFLICT (version) DO NOTHING;

-- Make PostgREST pick up the new function without a restart.
NOTIFY pgrst, 'reload schema';
