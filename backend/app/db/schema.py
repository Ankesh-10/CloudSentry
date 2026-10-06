"""Startup check that the database schema is at least the version this code needs.

Migrations are plain SQL files in db/migrations (NNN_name.sql). Each applied
version is recorded in schema_migrations, either by scripts/apply_migrations.py
or by the migration itself (011 onwards insert their own row; 011 backfills
001-010 for databases migrated by hand in the Supabase SQL editor).

Booting new code against an old schema fails in confusing ways later (an audit
insert into a missing column blocks every action), so refuse to start instead.
"""
import logging
import os
import re

from backend.app.db.asyncpg_pool import get_pool

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "db", "migrations")
_FILE = re.compile(r"^(\d{3})_.+\.sql$")


def latest_migration_version(directory: str = MIGRATIONS_DIR) -> int:
    versions = [int(m.group(1)) for f in os.listdir(directory) if (m := _FILE.match(f))]
    if not versions:
        raise RuntimeError(f"No migrations found in {directory}")
    return max(versions)


class SchemaOutOfDate(RuntimeError):
    pass


async def check_schema_version() -> int:
    """Return the applied version, or raise SchemaOutOfDate."""
    required = latest_migration_version()
    pool = get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
        if not exists:
            raise SchemaOutOfDate(
                f"schema_migrations table missing: apply db/migrations up to {required:03d} "
                "(python scripts/apply_migrations.py)"
            )
        applied = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
    if applied < required:
        raise SchemaOutOfDate(
            f"Database schema is at {applied:03d}, this release needs {required:03d}: "
            "apply the missing migrations (python scripts/apply_migrations.py)"
        )
    logger.info("Database schema version %03d (required %03d)", applied, required)
    return applied
