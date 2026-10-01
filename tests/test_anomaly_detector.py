from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.anomaly_detector import AnomalyDetectorService

NOW = datetime.now(timezone.utc)


def _idle_records(hours=3, cpu=1.0, end=NOW):
    rows = []
    for i in range(int(hours * 12), 0, -1):
        t = end - timedelta(minutes=5 * i)
        rows.append({"time": t, "metric_name": "CPUUtilization", "value": cpu})
        rows.append({"time": t, "metric_name": "NetworkIn", "value": 100.0})
    return rows


def _resource(tags=None, state="running", rtype="ec2", metadata=None):
    return {"id": "r1", "resource_type": rtype, "state": state, "tags": tags if tags is not None else {},
            "metadata": metadata or {}}


@pytest.fixture
def detector(fake_db):
    return AnomalyDetectorService()


def _types(fake_db, status="active"):
    return sorted(a["anomaly_type"] for a in fake_db.rows("anomalies") if a["status"] == status)


def test_untagged_resource_is_still_checked_for_idle(detector, fake_db):
    detector.process_resource(_resource(tags={}), _idle_records(), NOW)
    assert _types(fake_db) == ["idle_compute", "untagged_resource"]
    idle = next(a for a in fake_db.rows("anomalies") if a["anomaly_type"] == "idle_compute")
    assert idle["features_snapshot"]["idle_score"] > 0.9


def test_anomaly_is_updated_not_duplicated(detector, fake_db):
    tags = {"Project": "p", "Owner": "o"}
    detector.process_resource(_resource(tags=tags), _idle_records(), NOW)
    detector.process_resource(_resource(tags=tags), _idle_records(), NOW + timedelta(minutes=5))
    assert _types(fake_db) == ["idle_compute"]


def test_stale_metrics_do_not_raise_idle(detector, fake_db):
    detector.process_resource(_resource(tags={"Project": "p", "Owner": "o"}),
                              _idle_records(end=NOW - timedelta(hours=2)), NOW)
    assert _types(fake_db) == []


def test_stopped_instance_resolves_idle(detector, fake_db):
    tags = {"Project": "p", "Owner": "o"}
    detector.process_resource(_resource(tags=tags), _idle_records(), NOW)
    detector.process_resource(_resource(tags=tags, state="stopped"), _idle_records(), NOW)
    assert _types(fake_db) == []
    assert _types(fake_db, "resolved") == ["idle_compute"]


def test_busy_instance_resolves_idle(detector, fake_db):
    tags = {"Project": "p", "Owner": "o"}
    detector.process_resource(_resource(tags=tags), _idle_records(), NOW)
    detector.process_resource(_resource(tags=tags), _idle_records(cpu=60.0), NOW)
    assert "idle_compute" not in _types(fake_db)


def test_tags_fixed_resolves_untagged(detector, fake_db):
    detector.process_resource(_resource(tags={}, rtype="s3"), [], NOW)
    assert _types(fake_db) == ["untagged_resource"]
    detector.process_resource(_resource(tags={"Project": "p", "Owner": "o"}, rtype="s3"), [], NOW)
    assert _types(fake_db) == []


def test_unreadable_tags_are_not_untagged(detector, fake_db):
    detector.process_resource(_resource(tags={}, rtype="lambda", metadata={"tags_unreadable": True}), [], NOW)
    assert _types(fake_db) == []


def test_unattached_volume(detector, fake_db):
    detector.process_resource(_resource(tags={"Project": "p", "Owner": "o"}, rtype="ebs", state="available"), [], NOW)
    assert _types(fake_db) == ["unused_volume"]


def test_runaway_lambda(detector, fake_db):
    rows = []
    for i in range(30, 0, -1):
        t = NOW - timedelta(minutes=5 * i)
        rows.append({"time": t, "metric_name": "Invocations", "value": 8500.0 if i <= 1 else 100.0})
        rows.append({"time": t, "metric_name": "Errors", "value": 0.0})
    detector.process_resource(_resource(tags={"Project": "p", "Owner": "o"}, rtype="lambda"), rows, NOW)
    assert _types(fake_db) == ["runaway_lambda"]


def test_nan_scores_are_sanitised(detector, fake_db):
    detector._record_anomaly("r1", {"anomaly_type": "x", "score": float("nan"), "confidence": float("inf")},
                             NOW.isoformat(), {"f": float("nan")})
    row = fake_db.rows("anomalies")[0]
    assert row["anomaly_score"] == 0.0 and row["confidence"] == 0.0 and row["features_snapshot"]["f"] is None
