"""Apply pending SQL migrations from db/migrations, in order.

Usage:
    python scripts/apply_migrations.py            # apply pending
    python scripts/apply_migrations.py --status   # show applied/pending only

Reads DATABASE_URL from the environment (or .env). Each pending file runs in
its own transaction and is recorded in schema_migrations; a failure stops the
run with nothing from that file applied.

A database migrated by hand (Supabase SQL editor) before 011 has no
schema_migrations table: applying 011 creates it and records 001-011, so the
runner never re-applies those. Never edit a migration that has been applied;
add a new one.
"""
import argparse
import asyncio
import os
import re
import sys

import asyncpg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS = os.path.join(ROOT, "db", "migrations")
_FILE = re.compile(r"^(\d{3})_.+\.sql$")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url and os.path.exists(os.path.join(ROOT, ".env")):
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not url:
        sys.exit("DATABASE_URL is not set")
    return url


def migration_files() -> list[tuple[int, str]]:
    found = sorted((int(m.group(1)), f) for f in os.listdir(MIGRATIONS) if (m := _FILE.match(f)))
    versions = [v for v, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        sys.exit(f"Migration numbering has gaps or duplicates: {versions}")
    return found


async def applied_versions(conn) -> set[int]:
    if not await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL"):
        return set()
    return {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}


async def main(status_only: bool) -> int:
    conn = await asyncpg.connect(_database_url(), statement_cache_size=0)
    try:
        done = await applied_versions(conn)
        pending = [(v, f) for v, f in migration_files() if v not in done]
        print(f"Applied: {sorted(done) or 'none'}")
        print(f"Pending: {[f for _, f in pending] or 'none'}")
        if status_only or not pending:
            return 0
        for version, name in pending:
            with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as f:
                sql = f.read()
            print(f"Applying {name} ...", flush=True)
            async with conn.transaction():
                await conn.execute(sql)
                if await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL"):
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1) ON CONFLICT DO NOTHING", version)
        print("All migrations applied.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="show applied/pending migrations and exit")
    sys.exit(asyncio.run(main(parser.parse_args().status)))
