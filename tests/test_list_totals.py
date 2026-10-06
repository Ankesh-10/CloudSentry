"""List endpoints report the unpaged total in X-Total-Count (exposed to CORS)
while the body stays a plain page of rows."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from backend.app.main import app

SECRET = os.environ["SUPABASE_JWT_SECRET"]
ISSUER = os.environ["SUPABASE_URL"] + "/auth/v1"
NOW = datetime.now(timezone.utc)


def _h(sub="user-1"):
    tok = jwt.encode({"sub": sub, "aud": "authenticated", "iss": ISSUER, "exp": int(time.time()) + 600},
                     SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


def _uuid(i):
    return f"{i:08d}-0000-0000-0000-000000000000"


@pytest.fixture
def client(fake_db):
    for i in range(7):
        ts = (NOW - timedelta(minutes=i)).isoformat()
        fake_db.rows("resources").append({"id": _uuid(i), "provider_id": f"i-{i}", "resource_type": "ec2",
                                          "region": "us-east-1", "name": f"n{i}",
                                          "state": "deleted" if i == 6 else "running", "tags": {},
                                          "metadata": {}, "protected": False})
        fake_db.rows("anomalies").append({"id": _uuid(100 + i), "resource_id": _uuid(i), "anomaly_type": "idle_compute",
                                          "status": "active" if i < 5 else "resolved", "detected_at": ts})
        fake_db.rows("optimization_actions").append({"id": _uuid(200 + i), "resource_id": _uuid(i),
                                                     "action_type": "stop_ec2", "status": "completed",
                                                     "created_at": ts})
        fake_db.rows("audit_logs").append({"id": _uuid(300 + i), "event_type": "state_change", "actor": "SYSTEM",
                                           "created_at": ts})
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path,total", [
    ("/api/v1/resources/?limit=2", 6),               # deleted excluded from the total too
    ("/api/v1/anomalies/?limit=2", 7),
    ("/api/v1/anomalies/?status=active&limit=2", 5),  # total follows the filters
    ("/api/v1/actions/?limit=3&offset=3", 7),
    ("/api/v1/audit-logs/?limit=2", 7),
])
def test_total_count_header(client, path, total):
    res = client.get(path, headers=_h())
    assert res.status_code == 200
    assert res.headers["X-Total-Count"] == str(total)
    assert isinstance(res.json(), list) and len(res.json()) <= 3


def test_total_count_header_is_exposed_to_browsers(client):
    res = client.get("/api/v1/actions/", headers={**_h(), "Origin": "http://localhost:5173"})
    exposed = res.headers.get("access-control-expose-headers", "")
    assert "X-Total-Count" in exposed and "X-Request-ID" in exposed
