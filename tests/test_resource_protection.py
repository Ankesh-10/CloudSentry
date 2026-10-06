"""Resource protection from the API: protect at once, unprotect only with a
second operator, always audited; and the safety layer honours the flag."""
import os
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.app.config import settings
from backend.app.main import app
from backend.app.services.safety_layer import SafetyLayer

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"
RID = "88888888-8888-8888-8888-888888888888"
URL = f"/api/v1/resources/{RID}/protection"


def _h(sub):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600},
                     SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client(fake_db):
    fake_db.rows("resources").append({"id": RID, "provider_id": "i-abc", "resource_type": "ec2", "region": "us-east-1",
                                      "state": "running", "protected": False, "tags": {}, "metadata": {}})
    with TestClient(app) as c:
        yield c


def _res(fake_db):
    return fake_db.rows("resources")[0]


def test_protect_is_immediate_audited_and_blocks_actions(client, fake_db):
    res = client.patch(URL, json={"protected": True}, headers=_h("operator-1"))
    assert res.status_code == 200 and res.json()["protected"] is True
    log = fake_db.rows("audit_logs")[-1]
    assert log["event_type"] == "resource_protection_changed" and log["resource_id"] == RID
    ok, reason = SafetyLayer().check_proposal("stop_ec2", RID)
    assert ok is False and "protected" in reason


def test_unprotect_needs_a_second_operator(client, fake_db):
    _res(fake_db)["protected"] = True
    first = client.patch(URL, json={"protected": False}, headers=_h("operator-1"))
    assert first.status_code == 202 and _res(fake_db)["protected"] is True
    assert client.patch(URL, json={"protected": False}, headers=_h("operator-1")).status_code == 409
    pending = client.get("/api/v1/system/config/pending", headers=_h("user-1")).json()
    assert [p["key"] for p in pending] == [f"RESOURCE:{RID}:protected"]
    second = client.patch(URL, json={"protected": False}, headers=_h("operator-2"))
    assert second.status_code == 200 and _res(fake_db)["protected"] is False
    assert fake_db.rows("audit_logs")[-1]["request_params"]["requested_by"] == "USER:operator-1"
    assert client.get("/api/v1/system/config/pending", headers=_h("user-1")).json() == []


def test_unprotect_direct_when_two_person_rule_off(client, fake_db, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_TWO_PERSON_CONFIG", False)
    _res(fake_db)["protected"] = True
    assert client.patch(URL, json={"protected": False}, headers=_h("operator-1")).status_code == 200
    assert _res(fake_db)["protected"] is False


def test_viewers_cannot_change_protection(client):
    assert client.patch(URL, json={"protected": True}, headers=_h("user-1")).status_code == 403


def test_unknown_resource_and_bad_body(client):
    other = "99999999-9999-9999-9999-999999999999"
    assert client.patch(f"/api/v1/resources/{other}/protection", json={"protected": True},
                        headers=_h("operator-1")).status_code == 404
    for body in ({}, {"protected": "maybe"}, {"protected": True, "tags": {}}):
        assert client.patch(URL, json=body, headers=_h("operator-1")).status_code == 422


def test_not_changed_without_audit(client, fake_db):
    fake_db.fail_tables["audit_logs"] = True
    assert client.patch(URL, json={"protected": True}, headers=_h("operator-1")).status_code == 503
    assert _res(fake_db)["protected"] is False
