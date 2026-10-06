"""Policy engine: every condition operator, approval gating, priority and scoping."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services.policy_engine import PolicyEngine

NOW = datetime.now(timezone.utc)
CTX = {"anomaly": {"confidence": 0.9, "anomaly_type": "idle_compute"},
       "resource": {"state": "running", "protected": False, "age_minutes": 45}}


def _engine():
    return PolicyEngine.__new__(PolicyEngine)


@pytest.mark.parametrize("cond,expected", [
    ({"field": "anomaly.confidence", "op": "lt", "value": 0.95}, True),
    ({"field": "anomaly.confidence", "op": "lt", "value": 0.9}, False),
    ({"field": "anomaly.confidence", "op": "lte", "value": 0.9}, True),
    ({"field": "resource.state", "op": "neq", "value": "stopped"}, True),
    ({"field": "resource.state", "op": "neq", "value": "running"}, False),
    ({"field": "resource.state", "op": "in", "value": ["running", "pending"]}, True),
    ({"field": "resource.state", "op": "in", "value": ["stopped"]}, False),
    ({"field": "resource.state", "op": "in", "value": None}, False),
    ({"field": "resource.state", "op": "regex", "value": ".*"}, False),        # unknown op fails closed
    ({"field": "resource.state", "op": "gt", "value": 5}, False),               # type mismatch fails closed
    ({"field": "resource.missing.deep", "op": "eq", "value": None}, False),
    ({"OR": []}, False),
    ({"AND": []}, False),
    ({"OR": [{"field": "resource.state", "op": "eq", "value": "x"},
             {"field": "resource.age_minutes", "op": "gte", "value": 30}]}, True),
    ("not a dict", False),
])
def test_condition_operators(cond, expected):
    assert _engine()._evaluate_condition(cond, CTX) is expected


def _seed(fake_db, policies, anomaly_type="idle_compute", rtype="ec2"):
    fake_db.rows("resources").append({"id": "r1", "resource_type": rtype, "state": "running", "tags": {},
                                      "protected": False, "metadata": {"instance_type": "t3.micro"},
                                      "first_seen": (NOW - timedelta(hours=5)).isoformat()})
    fake_db.rows("anomalies").append({"id": "an1", "resource_id": "r1", "anomaly_type": anomaly_type,
                                      "status": "active", "confidence": 0.9, "anomaly_score": 1.0,
                                      "severity": "MEDIUM", "features_snapshot": {"idle_score": 0.95},
                                      "detected_at": NOW.isoformat()})
    for i, p in enumerate(policies):
        fake_db.rows("policies").append({
            "id": f"p{i}", "enabled": True, "resource_type": rtype, "anomaly_type": anomaly_type,
            "conditions": json.dumps({"field": "anomaly.confidence", "op": "gte", "value": 0.5}),
            "action_type": "stop_ec2", "risk_level": "MEDIUM", "requires_approval": False, "priority": 100,
            **p})


def _actions(fake_db):
    return fake_db.rows("optimization_actions")


def test_requires_approval_creates_pending_approval(fake_db):
    _seed(fake_db, [{"requires_approval": True, "risk_level": "HIGH"}])
    assert PolicyEngine().evaluate_all() == 1
    action = _actions(fake_db)[0]
    assert action["status"] == "pending_approval" and action["requires_approval"] is True
    assert action["risk_level"] == "HIGH"
    assert action["estimated_savings_usd"] > 0  # stop_ec2 on a priced instance


def test_disabled_policy_is_ignored(fake_db):
    _seed(fake_db, [{"enabled": False}])
    assert PolicyEngine().evaluate_all() == 0


def test_lowest_priority_number_wins(fake_db):
    _seed(fake_db, [{"priority": 200, "action_type": "apply_tags"}, {"priority": 10, "action_type": "stop_ec2"}])
    PolicyEngine().evaluate_all()
    assert [a["action_type"] for a in _actions(fake_db)] == ["stop_ec2"]


def test_wildcard_resource_type_matches(fake_db):
    _seed(fake_db, [{"resource_type": "*", "action_type": "apply_tags"}])
    assert PolicyEngine().evaluate_all() == 1


def test_policy_for_other_anomaly_type_does_not_match(fake_db):
    _seed(fake_db, [{"anomaly_type": "runaway_lambda"}])
    assert PolicyEngine().evaluate_all() == 0


def test_invalid_json_conditions_are_skipped_not_matched(fake_db):
    _seed(fake_db, [{"conditions": "{not json"}])
    assert PolicyEngine().evaluate_all() == 0


def test_protected_resource_is_never_proposed(fake_db):
    _seed(fake_db, [{}])
    fake_db.rows("resources")[0]["tags"] = {"cloudsentry:protected": "true"}
    assert PolicyEngine().evaluate_all() == 0


def test_expired_proposal_is_proposed_again(fake_db):
    """Regression: expiry used to mark proposals 'rejected', which the engine
    treats as a human 'no' and never re-proposes - the anomaly was stuck."""
    from backend.app.services.action_runner import ActionRunner
    _seed(fake_db, [{}])
    assert PolicyEngine().evaluate_all() == 1
    stale = _actions(fake_db)[0]
    stale["created_at"] = (NOW - timedelta(days=10)).isoformat()
    assert ActionRunner().expire_stale_proposals() == 1
    assert stale["status"] == "failed" and stale["post_state"]["expired"] is True
    assert PolicyEngine().evaluate_all() == 1
    assert [a["status"] for a in _actions(fake_db)] == ["failed", "pending"]


def test_operator_rejection_is_not_reproposed(fake_db):
    _seed(fake_db, [{}])
    PolicyEngine().evaluate_all()
    _actions(fake_db)[0]["status"] = "rejected"
    assert PolicyEngine().evaluate_all() == 0


def test_recommend_review_policy_is_proposed_for_approval(fake_db):
    _seed(fake_db, [{"action_type": "recommend_review", "requires_approval": True}],
          anomaly_type="unused_volume", rtype="ebs")
    assert PolicyEngine().evaluate_all() == 1
    assert _actions(fake_db)[0]["status"] == "pending_approval"
