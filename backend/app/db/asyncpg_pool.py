import logging
from typing import Optional
from urllib.parse import parse_qs, urlparse

import asyncpg

from backend.app.config import settings

logger = logging.getLogger(__name__)

# Global pool instance
_pool: Optional[asyncpg.Pool] = None

TRANSACTION_POOLER_PORT = 6543


def is_transaction_pooler(url: str) -> bool:
    """Supabase's transaction pooler (port 6543, or an explicit pgbouncer=true)
    hands each transaction to a different server connection, which breaks
    session-level advisory locks and prepared statements."""
    try:
        parsed = urlparse(url)
        if parsed.port == TRANSACTION_POOLER_PORT:
            return True
        query = {k.lower(): v for k, v in parse_qs(parsed.query).items()}
        return any(v.lower() == "true" for v in query.get("pgbouncer", []))
    except ValueError:
        return False


async def init_db_pool(required: bool = True):
    """
    Initialize the asyncpg connection pool (FastAPI lifespan).

    With required=True a missing or unreachable database fails startup: the
    process exits and the platform restarts it, instead of serving with a
    silently broken pool.
    """
    global _pool
    if not settings.DATABASE_URL:
        if required:
            raise RuntimeError("DATABASE_URL is not configured")
        logger.warning("DATABASE_URL not configured. Time-series queries will fail.")
        return

    try:
        _pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=60,
        )
        logger.info("asyncpg connection pool initialized.")
    except Exception as e:
        # The DSN may contain a password; log only the error type.
        logger.error("Failed to initialize asyncpg pool (%s)", type(e).__name__)
        if required:
            raise RuntimeError("Could not connect to DATABASE_URL") from e


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
