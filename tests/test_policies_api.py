"""Policies API: read for viewers, audited toggles for operators, two-person
confirmation for changes that let the agent do more."""
import os
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.app.config import settings
from backend.app.main import app

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"
PID = "66666666-6666-6666-6666-666666666666"


def _h(sub):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600},
                     SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client(fake_db):
    fake_db.rows("policies").append({
        "id": PID, "name": "Auto-stop idle EC2", "enabled": True, "resource_type": "ec2",
        "anomaly_type": "idle_compute", "conditions": {"AND": []}, "action_type": "stop_ec2",
        "risk_level": "MEDIUM", "requires_approval": True, "priority": 100})
    with TestClient(app) as c:
        yield c


def _policy(fake_db):
    return fake_db.rows("policies")[0]


def _events(fake_db):
    return [l["event_type"] for l in fake_db.rows("audit_logs")]


def test_viewers_can_list_but_not_change(client, fake_db):
    res = client.get("/api/v1/policies/", headers=_h("user-1"))
    assert res.status_code == 200 and res.json()[0]["id"] == PID
    assert client.get(f"/api/v1/policies/{PID}", headers=_h("user-1")).json()["name"] == "Auto-stop idle EC2"
    assert client.patch(f"/api/v1/policies/{PID}", json={"enabled": False}, headers=_h("user-1")).status_code == 403
    assert client.get("/api/v1/policies/", headers=_h("stranger")).status_code == 403


def test_disabling_is_immediate_and_audited(client, fake_db):
    res = client.patch(f"/api/v1/policies/{PID}", json={"enabled": False}, headers=_h("operator-1"))
    assert res.status_code == 200 and _policy(fake_db)["enabled"] is False
    log = fake_db.rows("audit_logs")[-1]
    assert log["event_type"] == "policy_changed" and log["actor"] == "USER:operator-1"
    assert log["request_params"]["from"] is True and log["request_params"]["to"] is False


@pytest.mark.parametrize("field,value,start", [
    ("requires_approval", False, True),   # actions would run unattended
    ("enabled", True, False),             # policy starts proposing again
])
def test_loosening_needs_a_second_operator(client, fake_db, field, value, start):
    _policy(fake_db)[field] = start
    url = f"/api/v1/policies/{PID}"
    first = client.patch(url, json={field: value}, headers=_h("operator-1"))
    assert first.status_code == 202 and first.json()["status"] == "pending_confirmation"
    assert _policy(fake_db)[field] is start
    assert client.patch(url, json={field: value}, headers=_h("operator-1")).status_code == 409
    second = client.patch(url, json={field: value}, headers=_h("operator-2"))
    assert second.status_code == 200 and _policy(fake_db)[field] is value
    assert _events(fake_db) == ["policy_change_requested", "policy_changed"]
    assert fake_db.rows("audit_logs")[-1]["request_params"]["requested_by"] == "USER:operator-1"
    assert not [r for r in fake_db.rows("system_config") if r["key"].startswith("PENDING:POLICY:")]


def test_two_person_rule_off_applies_directly(client, fake_db, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_TWO_PERSON_CONFIG", False)
    res = client.patch(f"/api/v1/policies/{PID}", json={"requires_approval": False}, headers=_h("operator-1"))
    assert res.status_code == 200 and _policy(fake_db)["requires_approval"] is False


@pytest.mark.parametrize("body", [{}, {"enabled": True, "requires_approval": True}, {"priority": 1},
                                  {"action_type": "stop_ec2"}])
def test_only_one_toggle_per_request(client, body):
    res = client.patch(f"/api/v1/policies/{PID}", json=body, headers=_h("operator-1"))
    assert res.status_code in (400, 422)


def test_unknown_policy_is_404(client):
    other = "77777777-7777-7777-7777-777777777777"
    assert client.patch(f"/api/v1/policies/{other}", json={"enabled": False},
                        headers=_h("operator-1")).status_code == 404


def test_change_not_applied_without_audit(client, fake_db):
    fake_db.fail_tables["audit_logs"] = True
    res = client.patch(f"/api/v1/policies/{PID}", json={"enabled": False}, headers=_h("operator-1"))
    assert res.status_code == 503 and _policy(fake_db)["enabled"] is True
