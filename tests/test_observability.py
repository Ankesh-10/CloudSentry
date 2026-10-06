import json
import logging
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.app import metrics
from backend.app.config import settings
from backend.app.logging_config import JsonFormatter, RequestIdFilter, SecretScrubFilter, scrub
from backend.app.main import app
from backend.app.services import alerts, job_status

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"


def _h(sub):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600},
                     SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(autouse=True)
def _clean():
    metrics.reset()
    alerts.reset()
    yield
    metrics.reset()
    alerts.reset()


@pytest.fixture
def client(fake_db):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def webhook(monkeypatch):
    sent = []

    class _Resp:
        def raise_for_status(self):
            pass

    def post(url, json, timeout):
        sent.append((url, json))
        return _Resp()

    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "https://hooks.example/abc")
    monkeypatch.setattr(alerts.httpx, "post", post)
    return sent


# -- logging -----------------------------------------------------------------------

@pytest.mark.parametrize("raw,leak", [
    ("connect postgresql://postgres:hunter2pw@db.x:5432/postgres failed", "hunter2pw"),
    ("Authorization: Bearer abcdefghijklmnop123", "abcdefghijklmnop123"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEifQ.c2lnbmF0dXJlLXZhbHVl", "eyJzdWIiOiJ1c2VyLTEifQ"),
    ("key AKIAIOSFODNN7EXAMPLE used", "AKIAIOSFODNN7EXAMPLE"),
    # Found by fuzzing: a JWT glued to preceding text escaped the \b-anchored pattern.
    ("idXYZeyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEifQ.c2lnbmF0dXJlLXZhbHVl", "eyJzdWIiOiJ1c2VyLTEifQ"),
    ("password=s3cr3t-value retry", "s3cr3t-value"),
])
def test_secrets_are_scrubbed(raw, leak):
    assert leak not in scrub(raw)


def _record(msg, *args, exc_info=None, **extra):
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, msg, args, exc_info)
    for k, v in extra.items():
        setattr(record, k, v)
    SecretScrubFilter().filter(record)
    RequestIdFilter().filter(record)
    return json.loads(JsonFormatter().format(record))


def test_json_logs_include_extra_fields_and_scrub_args():
    out = _record("db %s down", "postgresql://u:pw123456@h/db", action_id="a1", attempt=2)
    assert out["action_id"] == "a1" and out["attempt"] == 2
    assert "pw123456" not in out["msg"]
    assert "request_id" not in out  # None outside a request


def test_json_logs_scrub_exception_text():
    try:
        raise RuntimeError("bad dsn postgresql://u:topsecret1@h/db")
    except RuntimeError:
        import sys
        out = _record("failed", exc_info=sys.exc_info())
    assert "topsecret1" not in out["exc"]


# -- alerts --------------------------------------------------------------------------

def test_alerts_disabled_without_webhook(monkeypatch):
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "")
    assert alerts.send("k", "hello") is False


def test_alert_is_sent_scrubbed_and_throttled(webhook):
    assert alerts.send("k", "failed with password=hunter2xyz") is True
    assert alerts.send("k", "again") is False           # same key within the window
    assert alerts.send("other", "different") is True
    assert len(webhook) == 2
    assert "hunter2xyz" not in webhook[0][1]["text"]


def test_alert_delivery_failure_is_not_raised(monkeypatch):
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "https://hooks.example/abc")

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(alerts.httpx, "post", boom)
    assert alerts.send("k", "x") is False
    assert 'result="failed"' in metrics.render()


def test_job_alert_only_after_repeated_failures(webhook):
    assert alerts.job_failing("discovery", {"consecutive_failures": 2}) is None
    assert alerts.job_failing("discovery", {"consecutive_failures": 3, "last_error": "boom"}) is True
    assert "discovery" in webhook[0][1]["text"]


def test_emergency_stop_alerts(client, webhook):
    assert client.post("/api/v1/system/emergency-stop", headers=_h("viewer")).status_code == 200
    assert any("Emergency stop by USER:viewer" in body["text"] for _, body in webhook)


def test_startup_rejects_plain_http_webhook(monkeypatch):
    from backend.app import main
    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", "http://hooks.example/abc")
    with pytest.raises(RuntimeError, match="https"):
        main.validate_startup_config()


# -- /metrics -------------------------------------------------------------------------

def test_metrics_endpoint_disabled_without_token(client):
    assert client.get("/metrics").status_code == 404


def test_metrics_endpoint_requires_token(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_TOKEN", "scrape-token-123")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_metrics_report_route_templates_not_raw_paths(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_TOKEN", "scrape-token-123")
    client.get("/api/v1/actions/11111111-1111-1111-1111-111111111111", headers=_h("user-1"))
    job_status.record("discovery", False, "x")
    try:
        body = client.get("/metrics", headers={"Authorization": "Bearer scrape-token-123"}).text
    finally:
        job_status._status.clear()
    assert "# TYPE cloudsentry_http_requests_total counter" in body
    assert 'route="/api/v1/actions/{action_id}"' in body
    assert "11111111-1111" not in body
    assert 'cloudsentry_job_consecutive_failures{job="discovery"} 1' in body
