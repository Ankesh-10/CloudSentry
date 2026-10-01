import asyncio
import os
import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.app.config import settings
from backend.app.main import app
from backend.app.services import runtime_config

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"
ANOMALY_ID = "22222222-2222-2222-2222-222222222222"


def _h(sub):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "role": "authenticated", "iss": ISSUER,
                      "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client(fake_db):
    with TestClient(app) as c:
        yield c


# -- request correlation -------------------------------------------------------

def test_audit_rows_carry_request_id_and_client_ip(client, fake_db):
    res = client.patch("/api/v1/system/config", json={"key": "MAX_ACTIONS_PER_DAY", "value": "3"},
                       headers=_h("operator-1"))
    assert res.status_code == 200
    rid = res.headers["X-Request-ID"]
    rows = fake_db.rows("audit_logs")
    assert rows and all(r["request_id"] == rid for r in rows)
    assert all(r.get("client_ip") for r in rows)


def test_safe_caller_request_id_is_echoed(client):
    res = client.get("/api/v1/health/live", headers={"X-Request-ID": "trace-abc123"})
    assert res.headers["X-Request-ID"] == "trace-abc123"


@pytest.mark.parametrize("bad", ["short", "x" * 65, "abc def ghi", "a;b<script>"])
def test_unsafe_caller_request_id_is_replaced(client, bad):
    res = client.get("/api/v1/health/live", headers={"X-Request-ID": bad})
    assert res.headers["X-Request-ID"] != bad
    assert len(res.headers["X-Request-ID"]) == 32


# -- audit-before-change -------------------------------------------------------

def test_config_change_refused_when_audit_unavailable(client, fake_db):
    fake_db.fail_tables["audit_logs"] = True
    res = client.patch("/api/v1/system/config", json={"key": "MAX_ACTIONS_PER_DAY", "value": "3"},
                       headers=_h("operator-1"))
    assert res.status_code == 503
    assert fake_db.rows("system_config") == []
    assert runtime_config.get_flag("MAX_ACTIONS_PER_DAY") == 5


def test_invalid_config_is_rejected_before_any_audit_row(client, fake_db):
    res = client.patch("/api/v1/system/config", json={"key": "MAX_ACTIONS_PER_DAY", "value": "nan"},
                       headers=_h("operator-1"))
    assert res.status_code == 400
    assert fake_db.rows("audit_logs") == []


def test_emergency_stop_still_works_when_audit_unavailable(client, fake_db):
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    fake_db.fail_tables["audit_logs"] = True
    res = client.post("/api/v1/system/emergency-stop", headers=_h("viewer"))
    assert res.status_code == 200
    assert res.json()["audit_logged"] is False
    assert runtime_config.automation_enabled() is False


def _seed_anomaly(fake_db, status="active"):
    fake_db.rows("anomalies").append({"id": ANOMALY_ID, "resource_id": None, "anomaly_type": "idle_compute",
                                      "status": status, "detected_at": "2026-01-01T00:00:00+00:00"})


def test_anomaly_change_refused_when_audit_unavailable(client, fake_db):
    _seed_anomaly(fake_db)
    fake_db.fail_tables["audit_logs"] = True
    res = client.patch(f"/api/v1/anomalies/{ANOMALY_ID}", json={"status": "false_positive"},
                       headers=_h("operator-1"))
    assert res.status_code == 503
    assert fake_db.rows("anomalies")[0]["status"] == "active"


def test_anomaly_change_is_audited_with_transition(client, fake_db):
    _seed_anomaly(fake_db)
    res = client.patch(f"/api/v1/anomalies/{ANOMALY_ID}/status", json={"status": "resolved"},
                       headers=_h("operator-1"))
    assert res.status_code == 200 and res.json()["status"] == "resolved"
    row = fake_db.rows("audit_logs")[0]
    assert row["event_type"] == "anomaly_status_changed" and row["actor"] == "USER:operator-1"
    assert row["request_params"] == {"anomaly_id": ANOMALY_ID, "from": "active", "to": "resolved"}


def test_anomaly_unknown_id_is_404_without_audit(client, fake_db):
    res = client.patch(f"/api/v1/anomalies/{ANOMALY_ID}", json={"status": "resolved"}, headers=_h("operator-1"))
    assert res.status_code == 404
    assert fake_db.rows("audit_logs") == []


# -- retention -----------------------------------------------------------------

class _Conn:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return "DELETE 0"

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        @asynccontextmanager
        async def ctx():
            yield conn
        return ctx()


def _run_retention(monkeypatch, **overrides):
    from backend.app.services import retention
    conn = _Conn()
    monkeypatch.setattr(retention, "get_pool", lambda: _Pool(conn))
    for k, v in overrides.items():
        monkeypatch.setattr(settings, k, v)
    asyncio.run(retention.RetentionService().run())
    return conn.calls


def test_retention_prunes_history_children_first(monkeypatch):
    calls = _run_retention(monkeypatch, AUDIT_RETENTION_DAYS=400, HISTORY_RETENTION_DAYS=200)
    tables = [sql.split()[2] for sql, _ in calls]
    assert tables == ["resource_metrics", "cost_records", "audit_logs", "optimization_actions", "anomalies"]
    assert calls[2][1] == (400,)
    assert calls[3][1][0] == 200 and "completed" in calls[3][1][1]
    assert "active" not in calls[4][1][1]
    # Referenced rows are never deleted out from under their referrers.
    assert "NOT EXISTS" in calls[3][0] and "NOT EXISTS" in calls[4][0]


def test_retention_never_goes_below_trigger_floor(monkeypatch):
    calls = _run_retention(monkeypatch, AUDIT_RETENTION_DAYS=7, HISTORY_RETENTION_DAYS=1)
    assert calls[2][1] == (90,)
    assert calls[3][1][0] == 90 and calls[4][1][0] == 90


def test_migration_008_makes_audit_append_only():
    path = os.path.join(os.path.dirname(__file__), "..", "db", "migrations", "008_audit_integrity.sql")
    sql = open(path, encoding="utf-8").read()
    for needle in ("BEFORE UPDATE ON audit_logs", "BEFORE DELETE ON audit_logs", "BEFORE TRUNCATE ON audit_logs",
                   "INTERVAL '90 days'", "ADD COLUMN IF NOT EXISTS request_id"):
        assert needle in sql
    from backend.app.services.retention import MIN_RETENTION_DAYS
    assert MIN_RETENTION_DAYS == 90
