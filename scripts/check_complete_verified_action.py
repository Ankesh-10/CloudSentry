"""CI check: complete_verified_action (migration 014) on a real Postgres.

The pytest suite runs against an in-memory fake, so this is what proves the
SQL itself: the forward and rollback paths, the "no longer executing" guard,
all-or-nothing on a mid-function failure, and that end-user roles cannot call
it. Seeds its own rows; run after scripts/apply_migrations.py.

    DATABASE_URL=postgresql://... python scripts/check_complete_verified_action.py
"""
import asyncio
import json
import os

import asyncpg

SIG = "complete_verified_action(uuid,jsonb,text,boolean,text)"
CALL = "SELECT complete_verified_action($1, $2::jsonb, $3, $4, $5)"


ACCOUNT = {}


class _Rollback(Exception):
    pass


async def _account(conn):
    # resources.account_id is NOT NULL (migration 009).
    return await conn.fetchval(
        "INSERT INTO cloud_accounts (provider, account_id, region) "
        "VALUES ('aws', '000000000000', 'us-east-1') RETURNING id")


async def _seed(conn, tag: str, *, action_type="stop_ec2", status="executing", anomaly=True):
    rid = await conn.fetchval(
        "INSERT INTO resources (account_id, provider_id, resource_type, region, state) "
        "VALUES ($1, $2, 'ec2', 'us-east-1', 'running') RETURNING id", ACCOUNT["id"], f"i-ci-verify-{tag}")
    aid = None
    if anomaly:
        aid = await conn.fetchval(
            "INSERT INTO anomalies (resource_id, anomaly_type, status) "
            "VALUES ($1, 'idle_compute', 'active') RETURNING id", rid)
    act = await conn.fetchval(
        "INSERT INTO optimization_actions (anomaly_id, resource_id, action_type, status, dry_run) "
        "VALUES ($1, $2, $3, $4, false) RETURNING id", aid, rid, action_type, status)
    return rid, aid, act


async def _verified_rows(conn, action_id) -> int:
    return await conn.fetchval(
        "SELECT COUNT(*) FROM audit_logs WHERE action_id = $1 AND event_type = 'action_verified'", action_id)


async def check_forward(conn):
    rid, aid, act = await _seed(conn, "fwd")
    assert await conn.fetchval(CALL, act, json.dumps("stopped"), "stopped", False, "Verified target state: stopped") is True
    row = await conn.fetchrow("SELECT status, verified_at, post_state FROM optimization_actions WHERE id = $1", act)
    assert row["status"] == "completed" and row["verified_at"] is not None, row
    assert json.loads(row["post_state"]) == {"state": "stopped", "verified": True}, row
    assert await conn.fetchval("SELECT state FROM resources WHERE id = $1", rid) == "stopped"
    assert await conn.fetchval("SELECT status FROM anomalies WHERE id = $1", aid) == "resolved"
    assert await _verified_rows(conn, act) == 1

    # Second call: the action is no longer executing -> false, nothing written.
    assert await conn.fetchval(CALL, act, json.dumps("stopped"), "stopped", False, "again") is False
    assert await _verified_rows(conn, act) == 1


async def check_rollback(conn):
    _, aid, original = await _seed(conn, "orig", status="completed")
    rollback = await conn.fetchval(
        "INSERT INTO optimization_actions (anomaly_id, resource_id, action_type, status, dry_run) "
        "SELECT anomaly_id, resource_id, 'start_ec2', 'executing', false FROM optimization_actions WHERE id = $1 "
        "RETURNING id", original)
    await conn.execute("UPDATE optimization_actions SET rollback_action_id = $1 WHERE id = $2", rollback, original)
    assert await conn.fetchval(CALL, rollback, json.dumps("running"), "running", True, "Verified target state: running") is True
    assert await conn.fetchval("SELECT status FROM optimization_actions WHERE id = $1", rollback) == "completed"
    assert await conn.fetchval("SELECT status FROM optimization_actions WHERE id = $1", original) == "rolled_back"
    # A rollback does not touch the anomaly.
    assert await conn.fetchval("SELECT status FROM anomalies WHERE id = $1", aid) == "active"


async def check_all_or_nothing(conn):
    try:
        async with conn.transaction():
            rid, aid, act = await _seed(conn, "fail")
            await conn.execute(
                "CREATE FUNCTION pg_temp.ci_fail_audit() RETURNS trigger LANGUAGE plpgsql AS "
                "$f$ BEGIN RAISE EXCEPTION 'ci: audit insert refused'; END $f$")
            await conn.execute(
                "CREATE TRIGGER ci_fail_audit BEFORE INSERT ON audit_logs "
                "FOR EACH ROW EXECUTE FUNCTION pg_temp.ci_fail_audit()")
            try:
                async with conn.transaction():  # savepoint
                    await conn.fetchval(CALL, act, json.dumps("stopped"), "stopped", False, "x")
                raise AssertionError("audit failure did not abort the function")
            except asyncpg.RaiseError:
                pass
            # Step 1 (status) and step 2 (resource state) ran before the
            # failing audit insert; both must be rolled back with it.
            assert await conn.fetchval("SELECT status FROM optimization_actions WHERE id = $1", act) == "executing"
            assert await conn.fetchval("SELECT state FROM resources WHERE id = $1", rid) == "running"
            assert await conn.fetchval("SELECT status FROM anomalies WHERE id = $1", aid) == "active"
            raise _Rollback  # drop the trigger and the seeded rows
    except _Rollback:
        pass


async def check_privileges(conn):
    for role in ("anon", "authenticated"):
        assert await conn.fetchval("SELECT has_function_privilege($1, $2, 'EXECUTE')", role, SIG) is False, role


async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        ACCOUNT["id"] = await _account(conn)
        await check_forward(conn)
        await check_rollback(conn)
        await check_all_or_nothing(conn)
        await check_privileges(conn)
    finally:
        await conn.close()
    print("complete_verified_action: OK")


if __name__ == "__main__":
    asyncio.run(main())
