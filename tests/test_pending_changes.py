"""Pending two-person changes can be listed by viewers and withdrawn by any
operator (withdrawing is the safe direction), with an audit record."""
import os
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.app.main import app
from backend.app.services import runtime_config

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"


def _h(sub):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600},
                     SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client(fake_db):
    with TestClient(app) as c:
        yield c


def _request_enable(client):
    res = client.patch("/api/v1/system/config", json={"key": "GLOBAL_AUTOMATION_ENABLED", "value": "true"},
                       headers=_h("operator-1"))
    assert res.status_code == 202


def test_pending_changes_are_listed(client, fake_db):
    _request_enable(client)
    fake_db.rows("system_config").append({"key": "GLOBAL_AUTOMATION_ENABLED", "value": "false"})
    pending = client.get("/api/v1/system/config/pending", headers=_h("user-1")).json()
    assert len(pending) == 1
    p = pending[0]
    assert p["key"] == "GLOBAL_AUTOMATION_ENABLED" and p["value"] == "true"
    assert p["requested_by"] == "USER:operator-1" and p["expired"] is False and p["expires_at"]


def test_any_operator_can_cancel_and_it_is_audited(client, fake_db):
    _request_enable(client)
    res = client.post("/api/v1/system/config/pending/GLOBAL_AUTOMATION_ENABLED/cancel", headers=_h("operator-2"))
    assert res.status_code == 200
    assert runtime_config.list_pending() == []
    log = fake_db.rows("audit_logs")[-1]
    assert log["event_type"] == "config_change_cancelled" and log["actor"] == "USER:operator-2"
    # A cancelled request cannot be confirmed: the next call starts a new one.
    again = client.patch("/api/v1/system/config", json={"key": "GLOBAL_AUTOMATION_ENABLED", "value": "true"},
                         headers=_h("operator-2"))
    assert again.status_code == 202 and runtime_config.automation_enabled() is False


def test_cancel_unknown_key_is_404_without_audit(client, fake_db):
    res = client.post("/api/v1/system/config/pending/DRY_RUN_MODE/cancel", headers=_h("operator-1"))
    assert res.status_code == 404
    assert fake_db.rows("audit_logs") == []


def test_viewers_cannot_cancel(client):
    _request_enable(client)
    assert client.post("/api/v1/system/config/pending/GLOBAL_AUTOMATION_ENABLED/cancel",
                       headers=_h("user-1")).status_code == 403


def test_policy_toggle_requests_are_listed_too(client, fake_db):
    pid = "66666666-6666-6666-6666-666666666666"
    fake_db.rows("policies").append({"id": pid, "name": "p", "enabled": False, "requires_approval": True})
    assert client.patch(f"/api/v1/policies/{pid}", json={"enabled": True}, headers=_h("operator-1")).status_code == 202
    keys = [p["key"] for p in client.get("/api/v1/system/config/pending", headers=_h("user-1")).json()]
    assert keys == [f"POLICY:{pid}:enabled"]
    assert client.post(f"/api/v1/system/config/pending/POLICY:{pid}:enabled/cancel",
                       headers=_h("operator-2")).status_code == 200


@pytest.mark.parametrize("bad", ["a b", "x" * 101, "..etc"])
def test_cancel_rejects_malformed_keys(client, bad):
    res = client.post(f"/api/v1/system/config/pending/{bad}/cancel", headers=_h("operator-1"))
    assert res.status_code in (404, 422)
