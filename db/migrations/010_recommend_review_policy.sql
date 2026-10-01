-- 010: unused EBS volumes get a recommendation, not a tag write.
-- Idempotent. Run after 001-009.
--
-- The seeded "Recommend unused EBS volumes" policy mapped to apply_tags. On a
-- volume that already had the required tags that action completed as a no-op
-- ("nothing to apply") and resolved the anomaly, so nothing was ever actually
-- recommended. recommend_review records a recommendation that waits for a
-- human, never calls the cloud, and leaves the anomaly open until the volume is
-- really attached or deleted.

UPDATE policies
SET action_type = 'recommend_review',
    requires_approval = true,
    risk_level = 'LOW'
WHERE name = 'Recommend unused EBS volumes (never auto-delete)'
  AND action_type = 'apply_tags';

-- Proposals already queued under the old mapping would still run as tag
-- writes; retire them so the policy engine re-proposes the new action.
-- 'failed' (not 'rejected'): the engine never re-proposes for an anomaly with
-- a rejected action, but does after a failed one (once the cooldown passes).
UPDATE optimization_actions a
SET status = 'failed',
    post_state = COALESCE(a.post_state, '{}'::jsonb)
                 || '{"result": "Superseded: policy now recommends review (migration 010)"}'::jsonb
FROM anomalies n
WHERE a.anomaly_id = n.id
  AND n.anomaly_type = 'unused_volume'
  AND a.action_type = 'apply_tags'
  AND a.status IN ('pending', 'pending_approval');
