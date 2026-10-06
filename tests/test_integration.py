"""End-to-end: discovery -> telemetry -> detection -> policy -> execute ->
verify -> rollback, with real service code against moto AWS and an in-memory
PostgREST fake. Nothing in the pipeline under test is mocked out."""
import time
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from moto import mock_aws

from backend.app.config import settings
from backend.app.main import app
from backend.app.services import runtime_config
from backend.app.services.action_runner import ActionRunner
from backend.app.services.anomaly_detector import AnomalyDetectorService
from backend.app.services.discovery import DiscoveryService
from backend.app.services.policy_engine import PolicyEngine
from backend.app.services import telemetry as telemetry_mod

NOW = datetime.now(timezone.utc)


def _auth(sub):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "role": "authenticated", "exp": int(time.time()) + 600,
                      "iss": "https://example.supabase.co/auth/v1"},
                     "test-secret-key-for-hs256", algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


class _FakeConn:
    def __init__(self, sink):
        self.sink = sink

    async def executemany(self, sql, records):
        assert "ON CONFLICT" in sql
        for rec in records:
            self.sink.add(rec)


class _FakePool:
    def __init__(self, sink):
        self.sink = sink

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self_inner):
                return _FakeConn(pool.sink)

            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


@pytest.fixture
def world(fake_db):
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        iid = ec2.run_instances(
            ImageId="ami-12c6146b", MinCount=1, MaxCount=1, InstanceType="t2.micro",
            TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Project", "Value": "demo"},
                                                                     {"Key": "Owner", "Value": "me"}]}],
        )["Instances"][0]["InstanceId"]
        fake_db.rows("policies").append({
            "id": "p-idle", "name": "Auto-stop idle EC2", "enabled": True, "resource_type": "ec2",
            "anomaly_type": "idle_compute", "action_type": "stop_ec2", "risk_level": "MEDIUM",
            "requires_approval": False, "priority": 100,
            "conditions": {"AND": [
                {"field": "anomaly.idle_score", "op": "gte", "value": 0.80},
                {"field": "anomaly.confidence", "op": "gte", "value": 0.75},
                {"field": "resource.protected", "op": "eq", "value": False},
            ]},
        })
        yield {"db": fake_db, "iid": iid, "ec2": ec2}


def _state(world):
    return world["ec2"].describe_instances(InstanceIds=[world["iid"]])["Reservations"][0]["Instances"][0]["State"]["Name"]


@pytest.mark.asyncio
async def test_full_loop_idle_ec2(world, monkeypatch):
    db = world["db"]

    # 1. Discovery
    DiscoveryService().run()
    resource = next(r for r in db.rows("resources") if r["provider_id"] == world["iid"])

    # 2. Telemetry: CloudWatch -> resource_metrics (idempotent insert path).
    cw = boto3.client("cloudwatch", region_name="us-east-1")
    cw.put_metric_data(Namespace="AWS/EC2", MetricData=[{
        "MetricName": "CPUUtilization", "Dimensions": [{"Name": "InstanceId", "Value": world["iid"]}],
        "Timestamp": NOW - timedelta(minutes=10), "Value": 0.5, "Unit": "Percent"}])
    samples = set()
    monkeypatch.setattr(telemetry_mod, "get_pool", lambda: _FakePool(samples))
    await telemetry_mod.TelemetryService().run()
    assert any(s[1] == resource["id"] and s[2] == "CPUUtilization" for s in samples)

    # 3. Detection over a full idle window (collected history + this cycle).
    history = []
    for i in range(int(settings.ML_IDLE_WINDOW_HOURS * 12) + 12, 0, -1):
        t = NOW - timedelta(minutes=5 * i)
        history.append({"time": t, "metric_name": "CPUUtilization", "value": 0.5})
        history.append({"time": t, "metric_name": "NetworkIn", "value": 50.0})
    resource_row = dict(resource)
    AnomalyDetectorService().process_resource(resource_row, history, NOW)
    anomalies = [a for a in db.rows("anomalies") if a["status"] == "active"]
    assert [a["anomaly_type"] for a in anomalies] == ["idle_compute"]  # tagged, so no untagged anomaly

    # 4. Policy -> a pending proposal, even though automation is still off.
    resource["first_seen"] = (NOW - timedelta(hours=5)).isoformat()
    assert PolicyEngine().evaluate_all() == 1
    action = db.rows("optimization_actions")[0]
    assert action["status"] == "pending" and action["estimated_savings_usd"] > 0

    runner = ActionRunner()
    # Kill-switch off: the scheduled executor does nothing.
    assert runner.execute_pending_auto() == 0
    assert _state(world) == "running"

    # 5. Two operators enable live automation through the API: one requests,
    #    a different one confirms.
    with TestClient(app) as client:
        op = _auth("operator-1")
        op2 = _auth("operator-2")
        for key, value in (("DRY_RUN_MODE", "false"), ("GLOBAL_AUTOMATION_ENABLED", "true")):
            body = {"key": key, "value": value}
            assert client.patch("/api/v1/system/config", json=body, headers=op).status_code == 202
            assert client.patch("/api/v1/system/config", json=body, headers=op2).status_code == 200

        # 6. Scheduled executor + verifier close the loop.
        assert runner.execute_pending_auto() == 1
        assert _state(world) == "stopped"
        runner.verify_pending()
        action = db.rows("optimization_actions")[0]
        assert action["status"] == "completed" and action["dry_run"] is False
        assert db.rows("anomalies")[0]["status"] == "resolved"

        # No re-proposal for the resolved anomaly / stopped instance.
        assert PolicyEngine().evaluate_all() == 0

        # 7. Non-operator cannot roll back; operator can.
        assert client.post(f"/api/v1/actions/{action['id']}/rollback", headers=_auth("viewer")).status_code == 403
        res = client.post(f"/api/v1/actions/{action['id']}/rollback", headers=op)
        assert res.status_code == 202
    runner.verify_pending()
    assert _state(world) == "running"
    assert db.rows("optimization_actions")[0]["status"] == "rolled_back"

    events = [l["event_type"] for l in db.rows("audit_logs")]
    for expected in ("config_changed", "action_started", "action_submitted", "action_verified", "rollback_started"):
        assert expected in events
    assert any(l["actor"] == "USER:operator-1" for l in db.rows("audit_logs") if l["event_type"] == "rollback_started")


def test_approval_flow_and_emergency_stop(world):
    db = world["db"]
    DiscoveryService().run()
    resource = next(r for r in db.rows("resources") if r["provider_id"] == world["iid"])
    db.rows("optimization_actions").append({
        "id": "11111111-1111-1111-1111-111111111111", "resource_id": resource["id"], "action_type": "stop_ec2",
        "risk_level": "HIGH", "status": "pending_approval", "requires_approval": True, "dry_run": True,
        "created_at": NOW.isoformat()})
    aid = "11111111-1111-1111-1111-111111111111"
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    runtime_config.set_flag("DRY_RUN_MODE", False, persist=False)

    with TestClient(app) as client:
        # Scheduled executor must never run an unapproved HIGH-risk action.
        assert ActionRunner().execute_pending_auto() == 0
        assert client.post(f"/api/v1/actions/{aid}/approve", json={"approved": True}, headers=_auth("viewer")).status_code == 403

        # Emergency stop by any authenticated user, *then* the operator approves:
        # the approval is recorded but execution is blocked by the kill-switch.
        assert client.post("/api/v1/system/emergency-stop", headers=_auth("viewer")).status_code == 200
        res = client.post(f"/api/v1/actions/{aid}/approve", json={"approved": True}, headers=_auth("operator-1"))
        assert res.status_code == 200
        assert res.json()["approved_by"] == "USER:operator-1"
        # Double approval is a conflict, not a second execution.
        assert client.post(f"/api/v1/actions/{aid}/approve", json={"approved": True}, headers=_auth("operator-1")).status_code == 409

    assert _state(world) == "running"
    action = next(a for a in db.rows("optimization_actions") if a["id"] == aid)
    assert action["status"] == "failed"
    assert "Kill-switch" in action["post_state"]["result"]
