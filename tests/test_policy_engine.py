from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services import runtime_config
from backend.app.services.policy_engine import PolicyEngine

NOW = datetime.now(timezone.utc)

IDLE_POLICY = {
    "id": "p-idle", "name": "idle", "enabled": True, "resource_type": "ec2", "anomaly_type": "idle_compute",
    "conditions": {"AND": [
        {"field": "anomaly.idle_score", "op": "gte", "value": 0.80},
        {"field": "anomaly.confidence", "op": "gte", "value": 0.75},
        {"field": "resource.protected", "op": "eq", "value": False},
        {"field": "resource.age_minutes", "op": "gte", "value": 30},
    ]},
    "action_type": "stop_ec2", "risk_level": "MEDIUM", "requires_approval": False, "priority": 100,
}


def test_and_or_conditions():
    engine = PolicyEngine.__new__(PolicyEngine)
    context = {"anomaly": {"confidence": 0.9, "idle_score": 0.85}, "resource": {"protected": False, "age_minutes": 40}}
    assert engine._evaluate_condition(IDLE_POLICY["conditions"], context) is True


def test_empty_conditions_do_not_match():
    engine = PolicyEngine.__new__(PolicyEngine)
    assert engine._evaluate_condition({}, {"anomaly": {}}) is False
    assert engine._evaluate_condition({"AND": []}, {"anomaly": {}}) is False


def test_missing_field_does_not_match():
    engine = PolicyEngine.__new__(PolicyEngine)
    assert engine._evaluate_condition({"field": "anomaly.missing", "op": "eq", "value": 1}, {"anomaly": {}}) is False


def test_or_branch():
    engine = PolicyEngine.__new__(PolicyEngine)
    cond = {"OR": [{"field": "anomaly.confidence", "op": "gt", "value": 0.99},
                   {"field": "resource.protected", "op": "eq", "value": False}]}
    assert engine._evaluate_condition(cond, {"anomaly": {"confidence": 0.1}, "resource": {"protected": False}}) is True


@pytest.fixture
def seeded(fake_db):
    fake_db.rows("policies").append(dict(IDLE_POLICY))
    fake_db.rows("resources").append({
        "id": "r1", "provider_id": "i-1", "resource_type": "ec2", "state": "running", "protected": False, "tags": {},
        "metadata": {"instance_type": "t2.micro"}, "first_seen": (NOW - timedelta(hours=3)).isoformat()})
    fake_db.rows("anomalies").append({
        "id": "an1", "resource_id": "r1", "anomaly_type": "idle_compute", "status": "active", "confidence": 0.85,
        "anomaly_score": 0.98, "features_snapshot": {"idle_score": 0.98}, "detected_at": NOW.isoformat()})
    return fake_db


def test_proposes_even_with_kill_switch_off(seeded):
    """Proposals are recommendations; the kill-switch gates *execution*."""
    assert PolicyEngine().evaluate_all() == 1
    action = seeded.rows("optimization_actions")[0]
    assert action["action_type"] == "stop_ec2" and action["status"] == "pending"
    assert action["estimated_savings_usd"] == pytest.approx(0.0116 * 730, rel=1e-3)
    # No audit spam for proposals.
    assert seeded.rows("audit_logs") == []


def test_no_reproposal_after_completion(seeded):
    engine = PolicyEngine()
    engine.evaluate_all()
    seeded.rows("optimization_actions")[0]["status"] = "completed"  # e.g. a dry-run completion
    assert engine.evaluate_all() == 0
    assert len(seeded.rows("optimization_actions")) == 1


def test_failed_action_backs_off_then_retries(seeded):
    engine = PolicyEngine()
    engine.evaluate_all()
    action = seeded.rows("optimization_actions")[0]
    action["status"] = "failed"
    assert engine.evaluate_all() == 0  # within cooldown
    action["created_at"] = (NOW - timedelta(hours=2)).isoformat()
    assert engine.evaluate_all() == 1


def test_young_resource_not_matched(seeded):
    seeded.rows("resources")[0]["first_seen"] = NOW.isoformat()
    assert PolicyEngine().evaluate_all() == 0


def test_protected_resource_not_proposed(seeded):
    seeded.rows("resources")[0]["tags"] = {"cloudsentry:protected": "true"}
    assert PolicyEngine().evaluate_all() == 0


def test_lower_priority_number_wins(seeded):
    seeded.rows("policies").append({**IDLE_POLICY, "id": "p-first", "priority": 1, "action_type": "apply_tags",
                                     "risk_level": "LOW"})
    PolicyEngine().evaluate_all()
    assert seeded.rows("optimization_actions")[0]["action_type"] == "apply_tags"


def test_dry_run_flag_recorded_on_proposal(seeded):
    runtime_config.set_flag("DRY_RUN_MODE", False, persist=False)
    PolicyEngine().evaluate_all()
    assert seeded.rows("optimization_actions")[0]["dry_run"] is False
