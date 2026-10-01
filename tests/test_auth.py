import base64
import os
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jose import jwt

from backend.app import auth
from backend.app.main import app

SECRET = os.environ["SUPABASE_JWT_SECRET"]


def _claims(sub="user-1", **extra):
    base = {"sub": sub, "email": "dev@example.com", "role": "authenticated", "aud": "authenticated",
            "exp": int(time.time()) + 600}
    base.update(extra)
    return base


def _token(sub="user-1", **extra):
    return jwt.encode(_claims(sub, **extra), SECRET, algorithm="HS256")


def _h(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(fake_db):
    with TestClient(app) as c:
        yield c


def test_health_is_public(client):
    res = client.get("/api/v1/health")
    # No DB pool in tests -> unhealthy/503, but never 401.
    assert res.status_code in (200, 503)
    assert "last_error" not in str(res.json())


def test_liveness_is_public(client):
    assert client.get("/api/v1/health/live").status_code == 200


def test_resources_require_auth(client):
    assert client.get("/api/v1/resources/").status_code in (401, 403)


def test_forged_empty_key_rejected(client):
    bad = jwt.encode(_claims("attacker"), "", algorithm="HS256")
    assert client.get("/api/v1/resources/", headers=_h(bad)).status_code == 401


def test_alg_none_rejected(client):
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        f'{{"sub":"attacker","aud":"authenticated","exp":{int(time.time()) + 600}}}'.encode()
    ).rstrip(b"=").decode()
    assert client.get("/api/v1/resources/", headers=_h(f"{header}.{body}.")).status_code == 401


def test_expired_token_rejected(client):
    assert client.get("/api/v1/system/config", headers=_h(_token(exp=int(time.time()) - 10))).status_code == 401


def test_token_without_exp_rejected(client):
    claims = _claims()
    claims.pop("exp")
    tok = jwt.encode(claims, SECRET, algorithm="HS256")
    assert client.get("/api/v1/system/config", headers=_h(tok)).status_code == 401


def test_wrong_audience_rejected(client):
    assert client.get("/api/v1/system/config", headers=_h(_token(aud="anon"))).status_code == 401


def test_valid_token_can_read_config(client):
    res = client.get("/api/v1/system/config", headers=_h(_token()))
    assert res.status_code == 200
    assert "GLOBAL_AUTOMATION_ENABLED" in res.json()


def test_non_operator_cannot_change_config(client, fake_db):
    res = client.patch("/api/v1/system/config", json={"key": "GLOBAL_AUTOMATION_ENABLED", "value": "true"},
                       headers=_h(_token("user-1")))
    assert res.status_code == 403
    assert fake_db.rows("system_config") == []


def test_operator_by_id_can_change_config(client, fake_db):
    res = client.patch("/api/v1/system/config", json={"key": "MAX_ACTIONS_PER_DAY", "value": "3"},
                       headers=_h(_token("operator-1")))
    assert res.status_code == 200
    assert fake_db.rows("system_config")[0]["value"] == "3"
    assert fake_db.rows("audit_logs")[0]["actor"] == "USER:operator-1"


def test_operator_by_app_metadata(client):
    tok = _token("someone", app_metadata={"cloudsentry_role": "operator"})
    res = client.patch("/api/v1/system/config", json={"key": "DRY_RUN_MODE", "value": "true"}, headers=_h(tok))
    assert res.status_code == 200


def test_user_metadata_does_not_grant_operator(client):
    # user_metadata is user-editable in Supabase; it must never grant privileges.
    tok = _token("someone", user_metadata={"cloudsentry_role": "operator"})
    res = client.patch("/api/v1/system/config", json={"key": "DRY_RUN_MODE", "value": "true"}, headers=_h(tok))
    assert res.status_code == 403


def test_invalid_config_value_rejected(client):
    res = client.patch("/api/v1/system/config", json={"key": "GLOBAL_AUTOMATION_ENABLED", "value": "maybe"},
                       headers=_h(_token("operator-1")))
    assert res.status_code == 400


def test_nan_budget_rejected_over_api(client, fake_db):
    res = client.patch("/api/v1/system/config", json={"key": "MAX_DAILY_SPEND_USD", "value": "nan"},
                       headers=_h(_token("operator-1")))
    assert res.status_code == 400
    assert fake_db.rows("system_config") == []


def test_oversized_config_value_is_422(client):
    res = client.patch("/api/v1/system/config", json={"key": "MAX_ACTIONS_PER_DAY", "value": "1" * 65},
                       headers=_h(_token("operator-1")))
    assert res.status_code == 422


def test_enabling_automation_needs_two_operators(client, fake_db):
    from backend.app.services import runtime_config
    body = {"key": "GLOBAL_AUTOMATION_ENABLED", "value": "true"}
    res = client.patch("/api/v1/system/config", json=body, headers=_h(_token("operator-1")))
    assert res.status_code == 202 and res.json()["status"] == "pending_confirmation"
    assert runtime_config.automation_enabled() is False

    # The requester cannot confirm their own request, on either route.
    assert client.patch("/api/v1/system/config", json=body, headers=_h(_token("operator-1"))).status_code == 409
    assert client.patch("/api/v1/system/GLOBAL_AUTOMATION_ENABLED", json={"value": "true"},
                        headers=_h(_token("operator-1"))).status_code == 409

    res = client.patch("/api/v1/system/GLOBAL_AUTOMATION_ENABLED", json={"value": "true"},
                       headers=_h(_token("operator-2")))
    assert res.status_code == 200
    assert runtime_config.automation_enabled() is True
    events = [(l["event_type"], l["actor"]) for l in fake_db.rows("audit_logs")]
    assert ("config_change_requested", "USER:operator-1") in events
    assert ("config_changed", "USER:operator-2") in events


def test_any_user_can_emergency_stop(client, fake_db):
    from backend.app.services import runtime_config
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    res = client.post("/api/v1/system/emergency-stop", headers=_h(_token("user-1")))
    assert res.status_code == 200
    assert res.json()["persisted"] is True
    assert runtime_config.automation_enabled() is False
    assert fake_db.rows("system_config")[0]["value"] == "false"


def test_invalid_uuid_path_is_422(client):
    assert client.get("/api/v1/actions/not-a-uuid", headers=_h(_token())).status_code == 422


def test_es256_jwks_token_verified(client, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    nums = key.public_key().public_numbers()

    def b64(n):
        return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()

    jwk = {"kty": "EC", "crv": "P-256", "x": b64(nums.x), "y": b64(nums.y), "kid": "k1", "alg": "ES256", "use": "sig"}
    monkeypatch.setattr(auth, "_get_jwks", lambda force=False: [jwk])

    good = jwt.encode(_claims(), pem, algorithm="ES256", headers={"kid": "k1"})
    assert client.get("/api/v1/system/config", headers=_h(good)).status_code == 200

    other = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    forged = jwt.encode(_claims(), other, algorithm="ES256", headers={"kid": "k1"})
    assert client.get("/api/v1/system/config", headers=_h(forged)).status_code == 401
