import logging

from backend.app.config import settings
from backend.app.db.asyncpg_pool import get_pool

logger = logging.getLogger(__name__)

# Must match the floor enforced by the audit_logs trigger (migration 008):
# rows younger than this cannot be deleted at all.
MIN_RETENTION_DAYS = 90

TERMINAL_ACTION_STATUSES = ["completed", "failed", "rejected", "rolled_back"]
CLOSED_ANOMALY_STATUSES = ["resolved", "false_positive"]


def _days(value: int) -> int:
    return max(int(value), MIN_RETENTION_DAYS)


class RetentionService:
    async def run(self):
        logger.info("Starting retention cleanup...")
        try:
            pool = get_pool()
        except RuntimeError:
            raise RuntimeError("Retention skipped: database pool not initialized")
        audit_days = _days(settings.AUDIT_RETENTION_DAYS)
        history_days = _days(settings.HISTORY_RETENTION_DAYS)
        async with pool.acquire() as conn:
            metrics = await conn.execute(
                "DELETE FROM resource_metrics WHERE time < NOW() - INTERVAL '90 days'"
            )
            costs = await conn.execute(
                "DELETE FROM cost_records WHERE recorded_at < NOW() - INTERVAL '400 days'"
            )
            # Children before parents: audit rows reference actions, actions
            # reference anomalies and each other (rollback_action_id). Anything
            # still referenced is kept until its referrers age out.
            async with conn.transaction():
                audits = await conn.execute(
                    "DELETE FROM audit_logs WHERE created_at < NOW() - make_interval(days => $1)",
                    audit_days,
                )
                actions = await conn.execute(
                    """
                    DELETE FROM optimization_actions a
                    WHERE a.status = ANY($2::text[])
                      AND a.created_at < NOW() - make_interval(days => $1)
                      AND NOT EXISTS (SELECT 1 FROM audit_logs l WHERE l.action_id = a.id)
                      AND NOT EXISTS (SELECT 1 FROM optimization_actions r WHERE r.rollback_action_id = a.id)
                    """,
                    history_days, TERMINAL_ACTION_STATUSES,
                )
                anomalies = await conn.execute(
                    """
                    DELETE FROM anomalies n
                    WHERE n.status = ANY($2::text[])
                      AND n.detected_at < NOW() - make_interval(days => $1)
                      AND NOT EXISTS (SELECT 1 FROM optimization_actions a WHERE a.anomaly_id = n.id)
                    """,
                    history_days, CLOSED_ANOMALY_STATUSES,
                )
            logger.info(
                "Retention: metrics=%s cost_records=%s audit_logs=%s actions=%s anomalies=%s",
                metrics, costs, audits, actions, anomalies,
            )
