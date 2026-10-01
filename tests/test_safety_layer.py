from datetime import datetime, timedelta, timezone

import pytest

from backend.app.services import runtime_config
from backend.app.services.safety_layer import SafetyLayer

NOW = datetime.now(timezone.utc)


def _res(rid="r1", rtype="ec2", tags=None, protected=False):
    return {"id": rid, "resource_type": rtype, "tags": tags or {}, "protected": protected, "provider_id": f"p-{rid}"}


@pytest.fixture
def layer(fake_db):
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    return SafetyLayer()


def test_kill_switch_blocks(fake_db):
    fake_db.rows("resources").append(_res())
    ok, reason = SafetyLayer().check_execution("stop_ec2", "r1")
    assert ok is False and "Kill-switch" in reason


@pytest.mark.parametrize("tags,protected,action", [
    ({"cloudsentry:protected": "true"}, False, "stop_ec2"),
    ({"cloudsentry:exempt": "TRUE"}, False, "stop_ec2"),
    ({"do-not-stop": "yes"}, False, "stop_ec2"),
    ({}, True, "stop_ec2"),
])
def test_protection_blocks(layer, fake_db, tags, protected, action):
    fake_db.rows("resources").append(_res(tags=tags, protected=protected))
    ok, _ = layer.check_execution(action, "r1")
    assert ok is False


def test_destructive_action_blocked(layer, fake_db):
    fake_db.rows("resources").append(_res(rtype="ebs"))
    ok, reason = layer.check_execution("delete_ebs_volume", "r1")
    assert ok is False and "never allowed" in reason


def test_rds_state_change_blocked_but_tagging_allowed(layer, fake_db):
    fake_db.rows("resources").append(_res(rtype="rds"))
    assert layer.check_execution("stop_ec2", "r1")[0] is False
    assert layer.check_execution("apply_tags", "r1")[0] is True


def test_execution_recheck_ignores_the_action_itself(layer, fake_db):
    """Re-checking an already-claimed action must not see itself as a duplicate,
    as cooldown, or against the daily cap."""
    fake_db.rows("resources").append(_res())
    fake_db.rows("optimization_actions").append({
        "id": "a1", "resource_id": "r1", "action_type": "stop_ec2", "status": "executing",
        "dry_run": False, "executed_at": NOW.isoformat(),
    })
    assert layer.check_execution("stop_ec2", "r1", action_id="a1")[0] is True


def test_duplicate_in_flight_blocked(layer, fake_db):
    fake_db.rows("resources").append(_res())
    fake_db.rows("optimization_actions").append({"id": "a0", "resource_id": "r1", "action_type": "stop_ec2", "status": "pending"})
    ok, reason = layer.check_execution("stop_ec2", "r1", action_id="a1")
    assert ok is False and "already pending" in reason


def test_cooldown_counts_only_real_executions(layer, fake_db):
    fake_db.rows("resources").append(_res())
    recent = (NOW - timedelta(minutes=5)).isoformat()
    fake_db.rows("optimization_actions").append(
        {"id": "dry", "resource_id": "r1", "action_type": "limit_lambda", "status": "completed", "dry_run": True, "executed_at": recent})
    assert layer.check_execution("stop_ec2", "r1")[0] is True
    fake_db.rows("optimization_actions").append(
        {"id": "real", "resource_id": "r1", "action_type": "limit_lambda", "status": "completed", "dry_run": False, "executed_at": recent})
    ok, reason = layer.check_execution("stop_ec2", "r1")
    assert ok is False and "cooldown" in reason


def test_daily_cap(layer, fake_db):
    runtime_config.set_flag("MAX_ACTIONS_PER_DAY", 2, persist=False)
    fake_db.rows("resources").append(_res("target"))
    for i in range(2):
        fake_db.rows("optimization_actions").append({
            "id": f"x{i}", "resource_id": f"other{i}", "action_type": "stop_ec2", "status": "completed",
            "dry_run": False, "executed_at": NOW.isoformat()})
    ok, reason = layer.check_execution("stop_ec2", "target")
    assert ok is False and "Daily action limit" in reason


def test_tagging_does_not_consume_daily_cap(layer, fake_db):
    runtime_config.set_flag("MAX_ACTIONS_PER_DAY", 1, persist=False)
    fake_db.rows("resources").append(_res("target"))
    fake_db.rows("optimization_actions").append({
        "id": "t", "resource_id": "other", "action_type": "apply_tags", "status": "completed",
        "dry_run": False, "executed_at": NOW.isoformat()})
    assert layer.check_execution("stop_ec2", "target")[0] is True


def test_budget_gates_only_cost_increasing_actions(layer, fake_db):
    runtime_config.set_flag("MAX_DAILY_SPEND_USD", 1.0, persist=False)
    fake_db.rows("resources").append(_res())
    fake_db.rows("cost_records").append({"id": "c", "estimated_cost_usd": 5.0, "recorded_at": NOW.isoformat()})
    # Over budget: stopping (saves money) is still allowed; starting is not.
    assert layer.check_execution("stop_ec2", "r1")[0] is True
    ok, reason = layer.check_execution("start_ec2", "r1")
    assert ok is False and "spend cap" in reason


def test_budget_fails_closed_when_spend_unknown(layer, fake_db):
    fake_db.fail_tables["cost_records"] = True
    over, _ = layer.budget_status()
    assert over is True
