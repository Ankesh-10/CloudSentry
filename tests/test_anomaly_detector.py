from datetime import datetime, timedelta, timezone

import pytest

from backend.app.config import settings
from backend.app.services.anomaly_detector import AnomalyDetectorService

NOW = datetime.now(timezone.utc)


def _idle_records(hours=settings.ML_IDLE_WINDOW_HOURS + 1, cpu=1.0, end=NOW):
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


def test_redetection_refreshes_severity(detector, fake_db):
    low = {"is_anomaly": True, "anomaly_type": "unusual_cpu_spike", "severity": "LOW", "score": 3.0,
           "confidence": 0.5, "model_version": "zscore_fallback"}
    detector._record_anomaly("r1", low, NOW.isoformat(), {})
    detector._record_anomaly("r1", {**low, "severity": "HIGH", "score": 9.0, "model_version": "ewma_fallback"},
                             (NOW + timedelta(minutes=5)).isoformat(), {})
    rows = [a for a in fake_db.rows("anomalies") if a["anomaly_type"] == "unusual_cpu_spike"]
    assert len(rows) == 1
    assert (rows[0]["severity"], rows[0]["model_version"]) == ("HIGH", "ewma_fallback")


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


TAGS = {"Project": "p", "Owner": "o"}


def _seed_active(fake_db, anomaly_type, seen_ago, resource_id="r1"):
    fake_db.rows("anomalies").append({
        "id": f"an-{anomaly_type}-{resource_id}", "resource_id": resource_id, "anomaly_type": anomaly_type,
        "status": "active", "detected_at": (NOW - seen_ago).isoformat()})


def _lambda_rows(spike=False):
    rows = []
    for i in range(30, 0, -1):
        t = NOW - timedelta(minutes=5 * i)
        rows.append({"time": t, "metric_name": "Invocations", "value": 8500.0 if spike and i <= 1 else 100.0})
        rows.append({"time": t, "metric_name": "Errors", "value": 0.0})
    return rows


def test_runaway_lambda_resolves_once_quiet(detector, fake_db):
    _seed_active(fake_db, "runaway_lambda", timedelta(hours=1))
    detector.process_resource(_resource(tags=TAGS, rtype="lambda"), _lambda_rows(), NOW)
    assert _types(fake_db) == []
    assert _types(fake_db, "resolved") == ["runaway_lambda"]


def test_recently_seen_runaway_is_not_resolved_by_one_quiet_cycle(detector, fake_db):
    _seed_active(fake_db, "runaway_lambda", timedelta(minutes=5))
    detector.process_resource(_resource(tags=TAGS, rtype="lambda"), _lambda_rows(), NOW)
    assert _types(fake_db) == ["runaway_lambda"]


def test_ongoing_runaway_stays_active(detector, fake_db):
    _seed_active(fake_db, "runaway_lambda", timedelta(hours=1))
    detector.process_resource(_resource(tags=TAGS, rtype="lambda"), _lambda_rows(spike=True), NOW)
    assert _types(fake_db) == ["runaway_lambda"]


def test_statistical_anomaly_resolves_when_behaviour_normal(detector, fake_db):
    _seed_active(fake_db, "unusual_cpu_spike", timedelta(hours=1))
    _seed_active(fake_db, "ml_behavioral_anomaly", timedelta(hours=1))
    detector.process_resource(_resource(tags=TAGS), _idle_records(cpu=60.0), NOW)
    assert _types(fake_db) == []


def test_stopped_instance_resolves_behavioural_anomalies_immediately(detector, fake_db):
    _seed_active(fake_db, "unusual_cpu_spike", timedelta(minutes=1))
    detector.process_resource(_resource(tags=TAGS, state="stopped"), _idle_records(), NOW)
    assert _types(fake_db) == []


def test_stale_metrics_do_not_resolve_behavioural_anomalies(detector, fake_db):
    _seed_active(fake_db, "unusual_cpu_spike", timedelta(hours=3))
    detector.process_resource(_resource(tags=TAGS), _idle_records(end=NOW - timedelta(hours=2)), NOW)
    assert _types(fake_db) == ["unusual_cpu_spike"]


def test_terminated_resources_have_anomalies_resolved(detector, fake_db):
    fake_db.rows("resources").extend([
        {"id": "gone", "resource_type": "ec2", "state": "terminated"},
        {"id": "deleted", "resource_type": "s3", "state": "deleted"},
        {"id": "live", "resource_type": "ec2", "state": "running"},
    ])
    _seed_active(fake_db, "idle_compute", timedelta(hours=1), resource_id="gone")
    _seed_active(fake_db, "untagged_resource", timedelta(hours=1), resource_id="deleted")
    _seed_active(fake_db, "idle_compute", timedelta(hours=1), resource_id="live")
    assert detector.resolve_inactive_resources() == 2
    active = [a["resource_id"] for a in fake_db.rows("anomalies") if a["status"] == "active"]
    assert active == ["live"]


def test_nan_scores_are_sanitised(detector, fake_db):
    detector._record_anomaly("r1", {"anomaly_type": "x", "score": float("nan"), "confidence": float("inf")},
                             NOW.isoformat(), {"f": float("nan")})
    row = fake_db.rows("anomalies")[0]
    assert row["anomaly_score"] == 0.0 and row["confidence"] == 0.0 and row["features_snapshot"]["f"] is None
