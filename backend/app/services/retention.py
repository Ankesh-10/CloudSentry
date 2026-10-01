import logging
from backend.app.db.asyncpg_pool import get_pool

logger = logging.getLogger(__name__)


class RetentionService:
    async def run(self):
        logger.info("Starting metric retention cleanup...")
        try:
            pool = get_pool()
        except RuntimeError:
            raise RuntimeError("Retention skipped: database pool not initialized")
        async with pool.acquire() as conn:
            metrics = await conn.execute(
                "DELETE FROM resource_metrics WHERE time < NOW() - INTERVAL '90 days'"
            )
            costs = await conn.execute(
                "DELETE FROM cost_records WHERE recorded_at < NOW() - INTERVAL '400 days'"
            )
            logger.info("Retention: metrics=%s cost_records=%s", metrics, costs)
