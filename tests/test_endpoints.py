"""HTTP tests for the read endpoints: dashboard, metrics and audit logs."""
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.app.api import dashboard
from backend.app.api import metrics as metrics_api
from backend.app.main import app

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"
RID = "44444444-4444-4444-4444-444444444444"
NOW = datetime.now(timezone.utc)


def _h(sub="user-1"):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600},
                     SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


class _Conn:
    def __init__(self, row=None, rows=None):
        self.row, self.rows, self.calls = row, rows or [], []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self.row

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        @asynccontextmanager
        async def ctx():
            yield conn
        return ctx()


@pytest.fixture
def client(fake_db):
    with TestClient(app) as c:
        yield c


def _pool(monkeypatch, module, conn):
    monkeypatch.setattr(module, "get_pool", lambda: _Pool(conn))
    return conn


# -- dashboard -------------------------------------------------------------------------

def test_dashboard_summary(client, monkeypatch):
    _pool(monkeypatch, dashboard, _Conn(row={
        "active_resources": 7, "active_anomalies": 2, "potential_savings": 12.5,
        "realized_savings": 3.25, "month_to_date_cost": 4.0}))
    body = client.get("/api/v1/dashboard/summary", headers=_h()).json()
    assert body["active_resources"] == 7 and body["anomaly_count"] == 2
    assert body["potential_savings_usd"] == 12.5 and body["realized_savings_usd"] == 3.25
    assert body["automation_enabled"] is False and body["dry_run"] is True


def test_dashboard_without_pool_is_503(client):
    assert client.get("/api/v1/dashboard/overview", headers=_h()).status_code == 503


def test_cost_trend_bounds_and_shape(client, monkeypatch):
    conn = _pool(monkeypatch, dashboard, _Conn(rows=[{"day": date(2026, 10, 1), "cost": 1.5},
                                                     {"day": date(2026, 10, 2), "cost": None}]))
    body = client.get("/api/v1/dashboard/cost-trend?days=30", headers=_h()).json()
    assert body == [{"date": "2026-10-01", "cost_usd": 1.5}, {"date": "2026-10-02", "cost_usd": 0.0}]
    assert conn.calls[0][1] == (30,)
    assert client.get("/api/v1/dashboard/cost-trend?days=91", headers=_h()).status_code == 422


def test_anomaly_summary_fills_missing_severities(client, monkeypatch):
    _pool(monkeypatch, dashboard, _Conn(rows=[{"sev": "HIGH", "n": 2}, {"sev": "LOW", "n": 1},
                                              {"sev": "WEIRD", "n": 9}]))
    body = client.get("/api/v1/dashboard/anomaly-summary", headers=_h()).json()
    assert body == {"HIGH": 2, "MEDIUM": 0, "LOW": 1, "total": 3}


# -- metrics -----------------------------------------------------------------------------

def test_metric_summary_latest_per_metric(client, monkeypatch):
    _pool(monkeypatch, metrics_api, _Conn(rows=[{"metric_name": "CPUUtilization", "value": 3.5}]))
    assert client.get(f"/api/v1/metrics/{RID}/summary", headers=_h()).json() == {"CPUUtilization": 3.5}


def test_metric_series_filters_are_parameterised(client, monkeypatch):
    conn = _pool(monkeypatch, metrics_api, _Conn(rows=[]))
    res = client.get(f"/api/v1/metrics/{RID}?metric=CPU%27%3BDROP%20TABLE%20x--&days=2", headers=_h())
    assert res.status_code == 200
    sql, args = conn.calls[0]
    assert "DROP TABLE" not in sql            # value went in as a bind parameter
    assert args[3] == "CPU';DROP TABLE x--"
    assert "LIMIT 5000" in sql


def test_metric_series_rejects_inverted_range(client, monkeypatch):
    _pool(monkeypatch, metrics_api, _Conn())
    q = f"from={NOW.isoformat()}&to={(NOW - timedelta(days=1)).isoformat()}".replace("+", "%2B")
    assert client.get(f"/api/v1/metrics/{RID}?{q}", headers=_h()).status_code == 400


def test_metric_endpoints_validate_ids_and_bounds(client):
    assert client.get("/api/v1/metrics/not-a-uuid", headers=_h()).status_code == 422
    assert client.get(f"/api/v1/metrics/{RID}?days=91", headers=_h()).status_code == 422


# -- audit logs ----------------------------------------------------------------------------

def _seed_audit(fake_db):
    fake_db.rows("audit_logs").extend([
        {"id": "1", "event_type": "config_changed", "actor": "USER:operator-1", "request_id": "req-aaaaaaaa",
         "client_ip": "10.0.0.1", "created_at": (NOW - timedelta(hours=2)).isoformat()},
        {"id": "2", "event_type": "action_started", "actor": "SYSTEM", "created_at": (NOW - timedelta(hours=1)).isoformat()},
        {"id": "3", "event_type": "config_changed", "actor": "USER:operator-2", "created_at": NOW.isoformat()},
    ])


def test_audit_logs_newest_first_with_correlation_fields(client, fake_db):
    _seed_audit(fake_db)
    body = client.get("/api/v1/audit-logs/", headers=_h()).json()
    assert [r["id"] for r in body] == ["3", "2", "1"]
    assert body[2]["request_id"] == "req-aaaaaaaa" and body[2]["client_ip"] == "10.0.0.1"


@pytest.mark.parametrize("query,ids", [
    ("event_type=config_changed", ["3", "1"]),
    ("actor=SYSTEM", ["2"]),
    ("request_id=req-aaaaaaaa", ["1"]),
    ("limit=1&offset=1", ["2"]),
])
def test_audit_log_filters(client, fake_db, query, ids):
    _seed_audit(fake_db)
    assert [r["id"] for r in client.get(f"/api/v1/audit-logs/?{query}", headers=_h()).json()] == ids


def test_audit_log_time_window(client, fake_db):
    _seed_audit(fake_db)
    since = (NOW - timedelta(minutes=90)).isoformat().replace("+", "%2B")
    assert [r["id"] for r in client.get(f"/api/v1/audit-logs/?since={since}", headers=_h()).json()] == ["3", "2"]


@pytest.mark.parametrize("query", ["limit=501", "event_type=Bad-Type", "request_id=has%20space", "offset=-1"])
def test_audit_log_rejects_bad_params(client, query):
    assert client.get(f"/api/v1/audit-logs/?{query}", headers=_h()).status_code == 422


def test_audit_logs_need_viewer(client, fake_db):
    assert client.get("/api/v1/audit-logs/", headers=_h("stranger")).status_code == 403


# -- approval decisions are audited -----------------------------------------------------

AID = "55555555-5555-5555-5555-555555555555"


def _pending(fake_db):
    fake_db.rows("optimization_actions").append({
        "id": AID, "resource_id": RID, "action_type": "recommend_review", "status": "pending_approval",
        "requires_approval": True, "created_at": NOW.isoformat()})


@pytest.mark.parametrize("approved,event", [(True, "action_approved"), (False, "action_rejected")])
def test_approval_decisions_are_audited(client, fake_db, approved, event):
    _pending(fake_db)
    res = client.post(f"/api/v1/actions/{AID}/approve", json={"approved": approved}, headers=_h("operator-1"))
    assert res.status_code == 200
    log = next(l for l in fake_db.rows("audit_logs") if l["event_type"] == event)
    assert log["actor"] == "USER:operator-1" and log["action_id"] == AID
    assert log["request_params"] == {"from": "pending_approval", "to": event.removeprefix("action_")}


def test_decision_is_not_applied_without_an_audit_record(client, fake_db):
    _pending(fake_db)
    fake_db.fail_tables["audit_logs"] = True
    res = client.post(f"/api/v1/actions/{AID}/approve", json={"approved": False}, headers=_h("operator-1"))
    assert res.status_code == 503
    assert fake_db.rows("optimization_actions")[0]["status"] == "pending_approval"
