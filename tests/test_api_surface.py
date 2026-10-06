import os
import time

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.app import rate_limit
from backend.app.config import settings
from backend.app.main import app
from backend.app.services import health

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"


def _h(sub):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "role": "authenticated", "iss": ISSUER,
                      "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client(fake_db):
    with TestClient(app) as c:
        yield c


# -- security headers / docs / CORS ------------------------------------------

@pytest.mark.parametrize("path", ["/api/v1/health/live", "/api/v1/resources/"])
def test_security_headers_on_success_and_error(client, path):
    res = client.get(path)
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Cache-Control"] == "no-store"
    assert "max-age=" in res.headers["Strict-Transport-Security"]
    assert "default-src 'none'" in res.headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_disabled_by_default(client, path):
    assert client.get(path).status_code == 404


def test_cors_does_not_allow_credentials(client):
    res = client.options("/api/v1/resources/", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-credentials" not in res.headers


def test_cors_rejects_unknown_origin(client):
    res = client.options("/api/v1/resources/", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in res.headers


# -- public health -----------------------------------------------------------

def test_public_health_is_minimal(client):
    body = client.get("/api/v1/health").json()
    assert set(body) == {"status", "db"}
    assert set(body["db"]) == {"status"}


def test_public_health_is_cached_and_skips_budget(client, monkeypatch):
    probes = []

    async def fake_probe(body, issues, is_leader):
        probes.append(1)
        body["db"] = {"status": "ok", "latency_ms": 1.0}
        return True

    def boom(self):
        raise AssertionError("public probe must not compute the budget")

    from backend.app.services.safety_layer import SafetyLayer
    monkeypatch.setattr(health, "_probe_db", fake_probe)
    monkeypatch.setattr(SafetyLayer, "budget_status", boom)
    for _ in range(10):
        res = client.get("/api/v1/health")
        assert res.status_code == 200 and res.json()["status"] == "ok"
    assert len(probes) == 1


def test_detailed_health_caches_budget(client, monkeypatch):
    calls = []

    async def fake_probe(body, issues, is_leader):
        body["db"] = {"status": "ok", "latency_ms": 1.0}
        return True

    def budget(self):
        calls.append(1)
        return False, ""

    from backend.app.services.safety_layer import SafetyLayer
    monkeypatch.setattr(health, "_probe_db", fake_probe)
    monkeypatch.setattr(SafetyLayer, "budget_status", budget)
    for _ in range(3):
        body = client.get("/api/v1/system/health", headers=_h("user-1")).json()
        assert body["budget"] == {"over_budget": False, "reason": None}
    assert len(calls) == 1


def test_unwritable_model_dir_is_reported(client, monkeypatch):
    async def fake_probe(body, issues, is_leader):
        body["db"] = {"status": "ok", "latency_ms": 1.0}
        return True

    from backend.app.services.safety_layer import SafetyLayer
    monkeypatch.setattr(health, "_probe_db", fake_probe)
    monkeypatch.setattr(SafetyLayer, "budget_status", lambda self: (False, ""))
    monkeypatch.setattr(health, "models_dir_writable", lambda path: False)
    body = client.get("/api/v1/system/health", headers=_h("user-1")).json()
    assert body["ml_model"]["writable"] is False
    assert "model_dir_not_writable" in body["issues"] and body["status"] == "degraded"


def test_models_dir_writable_walks_up_to_an_existing_parent(tmp_path):
    assert health.models_dir_writable(str(tmp_path / "not" / "yet" / "created")) is True
    assert health.models_dir_writable(str(tmp_path)) is True


# -- rate limiting -------------------------------------------------------------

def test_sliding_window_limiter_expires_hits():
    now = [0.0]
    limiter = rate_limit.SlidingWindowLimiter(2, window=60, clock=lambda: now[0])
    assert limiter.hit("a") == 0 and limiter.hit("a") == 0
    assert limiter.hit("a") > 0
    assert limiter.hit("b") == 0  # keys are independent
    now[0] = 60.0
    assert limiter.hit("a") == 0


def test_per_ip_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 3)
    codes = [client.get("/api/v1/health/live").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    res = client.get("/api/v1/health/live")
    assert int(res.headers["Retry-After"]) >= 1


def test_rate_limit_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_PER_MINUTE", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    assert all(client.get("/api/v1/health/live").status_code == 200 for _ in range(5))


def test_discover_is_rate_limited_per_user(client, monkeypatch):
    from backend.app.services.discovery import DiscoveryService
    monkeypatch.setattr(DiscoveryService, "__init__", lambda self: None)
    monkeypatch.setattr(DiscoveryService, "run", lambda self: {"ec2": 0, "errors": []})
    codes = [client.post("/api/v1/resources/discover", headers=_h("operator-1")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    # A different operator has their own budget.
    assert client.post("/api/v1/resources/discover", headers=_h("operator-2")).status_code == 200


def test_discover_rejects_overlapping_runs(client):
    from backend.app.api import resources
    assert resources._discovery_lock.acquire(blocking=False)
    try:
        assert client.post("/api/v1/resources/discover", headers=_h("operator-1")).status_code == 409
    finally:
        resources._discovery_lock.release()


def test_discover_partial_failure_hides_details(client, monkeypatch):
    from backend.app.api import resources
    from backend.app.services.discovery import DiscoveryError, DiscoveryService

    def fail(self):
        raise DiscoveryError("Discovery failed for: rds", {"errors": ["rds"], "ec2": 3})

    monkeypatch.setattr(DiscoveryService, "__init__", lambda self: None)
    monkeypatch.setattr(DiscoveryService, "run", fail)
    res = client.post("/api/v1/resources/discover", headers=_h("operator-1"))
    assert res.status_code == 502
    assert res.json() == {"detail": "Discovery partially failed"}
    # The lock is released after a failure.
    assert resources._discovery_lock.acquire(blocking=False)
    resources._discovery_lock.release()


def test_emergency_stop_is_rate_limited(client):
    codes = [client.post("/api/v1/system/emergency-stop", headers=_h("viewer")).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_config_writes_are_rate_limited(client):
    body = {"key": "MAX_ACTIONS_PER_DAY", "value": "3"}
    codes = [client.patch("/api/v1/system/config", json=body, headers=_h("operator-1")).status_code
             for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
