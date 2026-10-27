"""TWO_PERSON_MODE=solo_delay: a lone operator may confirm their own loosening
change, but only after SOLO_CONFIRM_DELAY and before the request expires.
A different operator can still confirm at once; two_person mode is unchanged."""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.main import app
from backend.app.services import alerts, runtime_config
from backend.app.services.runtime_config import PENDING_PREFIX, PENDING_TTL, SOLO_CONFIRM_DELAY

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"
RID = "88888888-8888-8888-8888-888888888888"
PID = "66666666-6666-6666-6666-666666666666"


def _h(sub):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600},
                     SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def solo(monkeypatch):
    monkeypatch.setattr(settings, "TWO_PERSON_MODE", "solo_delay")


def _age_requests(fake_db, by: timedelta) -> None:
    """Move every pending request's requested_at back by `by`."""
    for row in fake_db.rows("system_config"):
        if row["key"].startswith(PENDING_PREFIX):
            pending = json.loads(row["value"])
            pending["requested_at"] = (datetime.fromisoformat(pending["requested_at"]) - by).isoformat()
            row["value"] = json.dumps(pending)


# ---- runtime config ---------------------------------------------------------

def test_requester_must_wait_then_can_confirm(fake_db, solo):
    first = runtime_config.request_change("DRY_RUN_MODE", "false", "USER:a")
    assert first["status"] == "pending_confirmation"
    confirm_at = datetime.fromisoformat(first["confirmable_at"])
    assert confirm_at - (datetime.fromisoformat(first["expires_at"]) - PENDING_TTL) == SOLO_CONFIRM_DELAY

    with pytest.raises(PermissionError, match="UTC"):
        runtime_config.request_change("DRY_RUN_MODE", "false", "USER:a")
    assert runtime_config.dry_run_mode() is True

    _age_requests(fake_db, SOLO_CONFIRM_DELAY + timedelta(seconds=5))
    res = runtime_config.request_change("DRY_RUN_MODE", "false", "USER:a")
    assert res["status"] == "applied" and res["self_confirmed"] is True and res["waited_minutes"] >= 15
    assert runtime_config.dry_run_mode() is False
    assert not [r for r in fake_db.rows("system_config") if r["key"].startswith(PENDING_PREFIX)]


def test_another_operator_still_confirms_at_once(fake_db, solo):
    runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:a")
    res = runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:b")
    assert res["status"] == "applied" and "self_confirmed" not in res
    assert runtime_config.automation_enabled() is True


def test_expired_request_cannot_be_self_confirmed(fake_db, solo):
    runtime_config.request_change("DRY_RUN_MODE", "false", "USER:a")
    _age_requests(fake_db, PENDING_TTL + timedelta(minutes=1))
    res = runtime_config.request_change("DRY_RUN_MODE", "false", "USER:a")
    assert res["status"] == "pending_confirmation"  # a fresh request, with a fresh wait
    assert runtime_config.dry_run_mode() is True


def test_two_person_mode_never_allows_self_confirmation(fake_db):
    assert settings.TWO_PERSON_MODE == "two_person"
    first = runtime_config.request_change("DRY_RUN_MODE", "false", "USER:a")
    assert first["confirmable_at"] is None
    _age_requests(fake_db, SOLO_CONFIRM_DELAY + timedelta(minutes=1))
    with pytest.raises(PermissionError, match="different operator"):
        runtime_config.request_change("DRY_RUN_MODE", "false", "USER:a")
    assert runtime_config.list_pending()[0]["confirmable_at"] is None


def test_list_pending_reports_when_requester_can_confirm(fake_db, solo):
    runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:a")
    [pending] = runtime_config.list_pending()
    requested_at = datetime.fromisoformat(pending["requested_at"])
    assert datetime.fromisoformat(pending["confirmable_at"]) == requested_at + SOLO_CONFIRM_DELAY


def test_emergency_stop_cancels_a_pending_self_confirmation(fake_db, solo):
    runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:a")
    runtime_config.emergency_stop()
    _age_requests(fake_db, SOLO_CONFIRM_DELAY + timedelta(minutes=1))
    res = runtime_config.request_change("GLOBAL_AUTOMATION_ENABLED", "true", "USER:a")
    assert res["status"] == "pending_confirmation"
    assert runtime_config.automation_enabled() is False


# ---- API --------------------------------------------------------------------

@pytest.fixture
def client(fake_db):
    fake_db.rows("resources").append({"id": RID, "provider_id": "i-abc", "resource_type": "ec2", "region": "us-east-1",
                                      "state": "running", "protected": True, "tags": {}, "metadata": {}})
    fake_db.rows("policies").append({
        "id": PID, "name": "Auto-stop idle EC2", "enabled": False, "resource_type": "ec2",
        "anomaly_type": "idle_compute", "conditions": {"AND": []}, "action_type": "stop_ec2",
        "risk_level": "MEDIUM", "requires_approval": True, "priority": 100})
    with TestClient(app) as c:
        yield c


def test_config_api_self_confirmation_is_audited(client, fake_db, solo):
    body = {"key": "DRY_RUN_MODE", "value": "false"}
    first = client.patch("/api/v1/system/config", json=body, headers=_h("operator-1"))
    assert first.status_code == 202 and first.json()["confirmable_at"]
    early = client.patch("/api/v1/system/config", json=body, headers=_h("operator-1"))
    assert early.status_code == 409 and "UTC" in early.json()["detail"]

    _age_requests(fake_db, SOLO_CONFIRM_DELAY + timedelta(seconds=5))
    done = client.patch("/api/v1/system/config", json=body, headers=_h("operator-1"))
    assert done.status_code == 200 and runtime_config.dry_run_mode() is False
    log = [r for r in fake_db.rows("audit_logs") if r["event_type"] == "config_changed"][-1]
    assert log["request_params"]["self_confirmed"] is True
    assert log["request_params"]["requested_by"] == "USER:operator-1"


def test_unprotect_can_be_self_confirmed_after_the_wait(client, fake_db, solo):
    url = f"/api/v1/resources/{RID}/protection"
    first = client.patch(url, json={"protected": False}, headers=_h("operator-1"))
    assert first.status_code == 202 and first.json()["confirmable_at"]
    assert client.patch(url, json={"protected": False}, headers=_h("operator-1")).status_code == 409
    _age_requests(fake_db, SOLO_CONFIRM_DELAY + timedelta(seconds=5))
    assert client.patch(url, json={"protected": False}, headers=_h("operator-1")).status_code == 200
    assert fake_db.rows("resources")[0]["protected"] is False
    assert fake_db.rows("audit_logs")[-1]["request_params"]["self_confirmed"] is True


def test_policy_enable_can_be_self_confirmed_after_the_wait(client, fake_db, solo):
    url = f"/api/v1/policies/{PID}"
    assert client.patch(url, json={"enabled": True}, headers=_h("operator-1")).status_code == 202
    assert client.patch(url, json={"enabled": True}, headers=_h("operator-1")).status_code == 409
    _age_requests(fake_db, SOLO_CONFIRM_DELAY + timedelta(seconds=5))
    assert client.patch(url, json={"enabled": True}, headers=_h("operator-1")).status_code == 200
    assert fake_db.rows("policies")[0]["enabled"] is True
    assert fake_db.rows("audit_logs")[-1]["request_params"]["self_confirmed"] is True


def test_request_sends_an_alert_with_the_confirm_time(client, fake_db, solo, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "send", lambda key, text: sent.append(text) or True)
    res = client.patch("/api/v1/system/config", json={"key": "DRY_RUN_MODE", "value": "false"},
                       headers=_h("operator-1"))
    assert res.status_code == 202
    assert len(sent) == 1 and "DRY_RUN_MODE=False" in sent[0]
    assert f"from {res.json()['confirmable_at'][11:16]} UTC" in sent[0]
