"""Single-leader election for the in-process scheduler.

The scheduler polls AWS and executes actions; two replicas running it would
double CloudWatch spend and race on actions. The leader holds a session-level
Postgres advisory lock on a dedicated connection. This needs a session-mode
connection (Supabase session pooler on port 5432, or a direct connection), not
the transaction pooler on port 6543.
"""
import logging
from typing import Optional

import asyncpg

from backend.app.config import settings

logger = logging.getLogger(__name__)

LOCK_KEY = 815_231_907  # arbitrary, stable app-wide key

_conn: Optional[asyncpg.Connection] = None


async def try_acquire() -> bool:
    global _conn
    if not settings.DATABASE_URL:
        return False
    if _conn is not None and not _conn.is_closed():
        return await is_leader()
    try:
        _conn = await asyncpg.connect(settings.DATABASE_URL, timeout=10, statement_cache_size=0)
        got = await _conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY)
    except Exception as e:
        logger.error("Leader election failed: %s", e)
        await release()
        return False
    if not got:
        await release()
        return False
    logger.info("Acquired scheduler leadership.")
    return True


async def is_leader() -> bool:
    if _conn is None or _conn.is_closed():
        return False
    try:
        await _conn.fetchval("SELECT 1")
        return True
    except Exception:
        # Connection lost => lock released server-side; another replica may lead.
        await release()
        return False


async def release() -> None:
    global _conn
    conn, _conn = _conn, None
    if conn is not None and not conn.is_closed():
        try:
            await conn.close()
        except Exception:
            pass
