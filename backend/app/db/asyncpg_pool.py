import asyncpg
from typing import Optional
from backend.app.config import settings
import logging

logger = logging.getLogger(__name__)

# Global pool instance
_pool: Optional[asyncpg.Pool] = None

async def init_db_pool():
    """
    Initialize the asyncpg connection pool.
    This should be called during FastAPI startup (lifespan).
    """
    global _pool
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL not configured. Time-series queries will fail.")
        return

    try:
        # Create a connection pool using the Session Pooler connection string
        _pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=60
        )
        logger.info("asyncpg connection pool initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize asyncpg pool: {e}")

async def close_db_pool():
    """
    Close the asyncpg connection pool.
    This should be called during FastAPI shutdown (lifespan).
    """
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("asyncpg connection pool closed.")

def get_pool() -> asyncpg.Pool:
    """
    Get the global asyncpg connection pool.
    """
    if not _pool:
        raise RuntimeError("Database pool is not initialized. Call init_db_pool first.")
    return _pool
