import io
import zipfile
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from backend.app.config import settings
from backend.app.services import runtime_config
from backend.app.services.action_runner import ActionRunner

NOW = datetime.now(timezone.utc)


@pytest.fixture
def aws():
    with mock_aws():
        yield


def _instance(tags=None):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    spec = [{"ResourceType": "instance", "Tags": [{"Key": k, "Value": v} for k, v in (tags or {}).items()]}] if tags else []
    kwargs = {"TagSpecifications": spec} if spec else {}
    return ec2.run_instances(ImageId="ami-12c6146b", MinCount=1, MaxCount=1, InstanceType="t2.micro", **kwargs)["Instances"][0]["InstanceId"]


def _state(iid):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    return ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]["State"]["Name"]


def _seed(fake_db, provider_id, rtype="ec2", status="pending", action_type="stop_ec2", requires_approval=False, **res_extra):
    fake_db.rows("resources").append({"id": "r1", "provider_id": provider_id, "resource_type": rtype, "state": "running",
                                      "tags": {}, "protected": False, "metadata": {}, **res_extra})
    fake_db.rows("anomalies").append({"id": "an1", "resource_id": "r1", "anomaly_type": "idle_compute", "status": "active"})
    fake_db.rows("optimization_actions").append({
        "id": "a1", "anomaly_id": "an1", "resource_id": "r1", "action_type": action_type, "risk_level": "MEDIUM",
        "status": status, "requires_approval": requires_approval, "dry_run": True, "created_at": NOW.isoformat()})


def _action(fake_db, aid="a1"):
    return next(a for a in fake_db.rows("optimization_actions") if a["id"] == aid)


def _live():
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    runtime_config.set_flag("DRY_RUN_MODE", False, persist=False)


def test_kill_switch_blocks_execution_and_is_audited(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    runner = ActionRunner()
    assert runner.execute_action("a1") is False
    assert _state(iid) == "running"
    assert _action(fake_db)["status"] == "failed"
    assert any(l["event_type"] == "action_blocked" for l in fake_db.rows("audit_logs"))


def test_db_kill_switch_overrides_stale_in_memory_flag(aws, fake_db):
    """A kill-switch written straight to system_config (another replica, or
    the Supabase dashboard) must stop execution even if this process thinks
    automation is on."""
    iid = _instance()
    _seed(fake_db, iid)
    _live()
    fake_db.rows("system_config").append({"key": "GLOBAL_AUTOMATION_ENABLED", "value": "false"})
    assert ActionRunner().execute_action("a1") is False
    assert _state(iid) == "running"


def test_unreadable_config_fails_closed(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    _live()
    fake_db.fail_tables["system_config"] = True
    assert ActionRunner().execute_action("a1") is False
    assert _state(iid) == "running"


def test_dry_run_never_calls_aws(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    assert ActionRunner().execute_action("a1") is True
    assert _state(iid) == "running"
    action = _action(fake_db)
    assert action["status"] == "completed" and action["dry_run"] is True
    assert "WOULD" in action["post_state"]["result"]
    # A dry run does not fix anything, so the anomaly stays active.
    assert fake_db.rows("anomalies")[0]["status"] == "active"


def test_requires_approval_is_not_executable_until_approved(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid, status="pending_approval", requires_approval=True)
    _live()
    assert ActionRunner().execute_action("a1") is False
    assert _state(iid) == "running"
    assert _action(fake_db)["status"] == "pending_approval"


def test_claim_is_exclusive(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    _live()
    runner = ActionRunner()
    assert runner.execute_action("a1") is True
    # Second caller (scheduler tick / API / other replica) cannot run it again.
    assert runner.execute_action("a1") is False
    started = [l for l in fake_db.rows("audit_logs") if l["event_type"] == "action_started"]
    assert len(started) == 1


def test_stop_verify_rollback_cycle(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    _live()
    runner = ActionRunner()

    assert runner.execute_action("a1") is True
    action = _action(fake_db)
    assert action["status"] == "executing" and action["dry_run"] is False
    assert action["pre_state"]["aws_state"] == "running"
    assert _state(iid) == "stopped"

    assert runner.verify_pending() == 1
    action = _action(fake_db)
    assert action["status"] == "completed" and action["post_state"]["verified"] is True
    assert fake_db.rows("anomalies")[0]["status"] == "resolved"
    assert fake_db.rows("resources")[0]["state"] == "stopped"

    assert runner.rollback_action("a1", "USER:op") is True
    assert _state(iid) == "running"
    rollback_id = _action(fake_db)["rollback_action_id"]
    assert _action(fake_db, rollback_id)["status"] == "executing"
    runner.verify_pending()
    assert _action(fake_db, rollback_id)["status"] == "completed"
    assert _action(fake_db)["status"] == "rolled_back"
    # Rollback cannot be repeated.
    assert runner.rollback_action("a1", "USER:op") is False


def test_rollback_of_dry_run_action_stays_dry(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    runner = ActionRunner()
    runner.execute_action("a1")  # dry-run completion
    runtime_config.set_flag("DRY_RUN_MODE", False, persist=False)
    boto3.client("ec2", region_name="us-east-1").stop_instances(InstanceIds=[iid])
    assert runner.rollback_action("a1", "USER:op") is True
    # The original never stopped anything, so the "undo" must not start anything.
    assert _state(iid) == "stopped"


def test_verify_timeout_marks_failed(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    action = _action(fake_db)
    action.update({"status": "executing", "dry_run": False,
                   "executed_at": (NOW - timedelta(minutes=10)).isoformat()})
    ActionRunner().verify_pending()  # instance still running -> never reached "stopped"
    assert _action(fake_db)["status"] == "failed"


def test_stale_claim_is_failed(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    _action(fake_db).update({"status": "executing", "claimed_at": (NOW - timedelta(hours=1)).isoformat()})
    ActionRunner().verify_pending()
    assert _action(fake_db)["status"] == "failed"
    assert "stale claim" in _action(fake_db)["post_state"]["result"]


def _lambda(name="fn"):
    iam = boto3.client("iam", region_name="us-east-1")
    role = iam.create_role(RoleName="r", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("h.py", "def handler(e, c): return 1")
    lam = boto3.client("lambda", region_name="us-east-1")
    return lam.create_function(FunctionName=name, Runtime="python3.11", Role=role, Handler="h.handler",
                               Code={"ZipFile": buf.getvalue()})["FunctionArn"]


def test_limit_lambda_sets_positive_cap_and_restores(aws, fake_db):
    arn = _lambda()
    _seed(fake_db, "fn", rtype="lambda", action_type="limit_lambda", metadata={"arn": arn})
    _live()
    runner = ActionRunner()
    assert runner.execute_action("a1") is True
    lam = boto3.client("lambda", region_name="us-east-1")
    assert lam.get_function_concurrency(FunctionName="fn")["ReservedConcurrentExecutions"] == settings.LAMBDA_CONCURRENCY_LIMIT
    assert settings.LAMBDA_CONCURRENCY_LIMIT > 0
    runner.verify_pending()
    assert _action(fake_db)["status"] == "completed"
    assert runner.rollback_action("a1", "USER:op") is True
    assert lam.get_function_concurrency(FunctionName="fn").get("ReservedConcurrentExecutions") is None


def test_zero_concurrency_refused(aws, fake_db, monkeypatch):
    arn = _lambda()
    _seed(fake_db, "fn", rtype="lambda", action_type="limit_lambda", metadata={"arn": arn})
    _live()
    monkeypatch.setattr(settings, "LAMBDA_CONCURRENCY_LIMIT", 0)
    assert ActionRunner().execute_action("a1") is False
    lam = boto3.client("lambda", region_name="us-east-1")
    assert lam.get_function_concurrency(FunctionName="fn").get("ReservedConcurrentExecutions") is None


# -- expiry ---------------------------------------------------------------------

def test_expired_proposal_is_not_executed(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    _action(fake_db)["created_at"] = (NOW - timedelta(hours=settings.APPROVAL_TTL_HOURS + 1)).isoformat()
    _live()
    assert ActionRunner().execute_action("a1") is False
    assert _state(iid) == "running"
    assert _action(fake_db)["status"] == "rejected"
    assert "Expired" in _action(fake_db)["post_state"]["result"]
    assert any(l["event_type"] == "action_expired" for l in fake_db.rows("audit_logs"))


def test_stale_approval_is_not_executed(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid, status="approved", requires_approval=True)
    _action(fake_db)["approved_at"] = (NOW - timedelta(hours=settings.APPROVAL_TTL_HOURS + 1)).isoformat()
    _live()
    assert ActionRunner().execute_action("a1") is False
    assert _state(iid) == "running"


def test_scheduler_sweep_expires_old_proposals(aws, fake_db):
    _seed(fake_db, "i-x", status="pending_approval", requires_approval=True)
    _action(fake_db)["created_at"] = (NOW - timedelta(days=10)).isoformat()
    runner = ActionRunner()
    runner.execute_pending_auto()
    assert _action(fake_db)["status"] == "rejected"
    # Nothing left to expire on a second sweep.
    assert runner.expire_stale_proposals() == 0


def test_approving_expired_proposal_is_409(aws, fake_db):
    import os
    import time
    from fastapi.testclient import TestClient
    from jose import jwt
    from backend.app.main import app

    _seed(fake_db, "i-x", status="pending_approval", requires_approval=True)
    aid = "33333333-3333-3333-3333-333333333333"
    _action(fake_db).update({"id": aid, "created_at": (NOW - timedelta(days=10)).isoformat()})
    tok = jwt.encode({"sub": "operator-1", "aud": "authenticated", "iss": os.environ["SUPABASE_URL"] + "/auth/v1",
                      "exp": int(time.time()) + 600}, os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")
    with TestClient(app) as client:
        res = client.post(f"/api/v1/actions/{aid}/approve", json={"approved": True},
                          headers={"Authorization": f"Bearer {tok}"})
    assert res.status_code == 409
    assert _action(fake_db, aid)["status"] == "rejected"


# -- execution-time re-validation ------------------------------------------------

def test_resolved_anomaly_blocks_execution(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    fake_db.rows("anomalies")[0]["status"] = "resolved"
    _live()
    assert ActionRunner().execute_action("a1") is False
    assert _state(iid) == "running"
    assert "anomaly is resolved" in _action(fake_db)["post_state"]["result"]


def test_live_state_mismatch_blocks_execution(aws, fake_db):
    iid = _instance()
    boto3.client("ec2", region_name="us-east-1").stop_instances(InstanceIds=[iid])
    _seed(fake_db, iid)  # DB still says running
    _live()
    assert ActionRunner().execute_action("a1") is False
    action = _action(fake_db)
    assert action["status"] == "failed" and "live state is stopped" in action["post_state"]["result"]
    assert action.get("pre_state") is None  # never reached the mutating call


def test_unreadable_live_state_blocks_execution(aws, fake_db, monkeypatch):
    iid = _instance()
    _seed(fake_db, iid)
    _live()
    runner = ActionRunner()
    monkeypatch.setattr(runner.cloud, "get_instance_state", lambda pid: None)
    assert runner.execute_action("a1") is False
    assert _state(iid) == "running"


# -- daily cap race ---------------------------------------------------------------

def test_claimed_but_unsubmitted_actions_count_toward_daily_cap(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    fake_db.rows("optimization_actions").append({
        "id": "other", "resource_id": "r-other", "action_type": "stop_ec2", "status": "executing",
        "dry_run": True, "claimed_at": NOW.isoformat(), "executed_at": None, "created_at": NOW.isoformat()})
    runtime_config.set_flag("MAX_ACTIONS_PER_DAY", 1, persist=False)
    _live()
    assert ActionRunner().execute_action("a1") is False
    assert "Daily action limit" in _action(fake_db)["post_state"]["result"]
    assert _state(iid) == "running"


def test_failed_after_submit_counts_toward_daily_cap(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    fake_db.rows("optimization_actions").append({
        "id": "other", "resource_id": "r-other", "action_type": "stop_ec2", "status": "failed",
        "dry_run": False, "pre_state": {"aws_state": "running"}, "executed_at": NOW.isoformat(),
        "created_at": NOW.isoformat()})
    runtime_config.set_flag("MAX_ACTIONS_PER_DAY", 1, persist=False)
    _live()
    assert ActionRunner().execute_action("a1") is False
    assert _state(iid) == "running"


def test_blocked_actions_do_not_count_toward_daily_cap(aws, fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    fake_db.rows("optimization_actions").append({
        "id": "other", "resource_id": "r-other", "action_type": "stop_ec2", "status": "failed",
        "dry_run": True, "executed_at": NOW.isoformat(), "created_at": NOW.isoformat()})
    runtime_config.set_flag("MAX_ACTIONS_PER_DAY", 1, persist=False)
    _live()
    assert ActionRunner().execute_action("a1") is True


# -- rollback ---------------------------------------------------------------------

def _completed_stop(fake_db):
    iid = _instance()
    _seed(fake_db, iid)
    _live()
    runner = ActionRunner()
    assert runner.execute_action("a1") is True
    assert runner.verify_pending() == 1
    return iid, runner


def test_failed_rollback_can_be_retried(aws, fake_db, monkeypatch):
    iid, runner = _completed_stop(fake_db)
    monkeypatch.setattr(runner.cloud, "start_instance", lambda pid: False)
    assert runner.rollback_action("a1", "USER:op") is False
    original = _action(fake_db)
    assert original["status"] == "completed" and original.get("rollback_action_id") is None
    monkeypatch.undo()
    assert runner.rollback_action("a1", "USER:op") is True
    assert _state(iid) == "running"


def test_rollback_verify_timeout_releases_link(aws, fake_db, monkeypatch):
    iid, runner = _completed_stop(fake_db)
    assert runner.rollback_action("a1", "USER:op") is True
    rollback_id = _action(fake_db)["rollback_action_id"]
    _action(fake_db, rollback_id)["executed_at"] = (NOW - timedelta(minutes=10)).isoformat()
    monkeypatch.setattr(runner.cloud, "get_instance_state", lambda pid: "pending")
    runner.verify_pending()
    assert _action(fake_db, rollback_id)["status"] == "failed"
    assert _action(fake_db).get("rollback_action_id") is None


def test_rollback_blocked_for_protected_resource(aws, fake_db):
    iid, runner = _completed_stop(fake_db)
    fake_db.rows("resources")[0]["tags"] = {"cloudsentry:protected": "true"}
    assert runner.rollback_action("a1", "USER:op") is False
    assert _state(iid) == "stopped"
    assert any(l["event_type"] == "rollback_blocked" for l in fake_db.rows("audit_logs"))
    assert _action(fake_db).get("rollback_action_id") is None


def test_rollback_that_raises_spend_is_budget_gated(aws, fake_db):
    iid, runner = _completed_stop(fake_db)
    fake_db.rows("cost_records").append({"resource_id": "r1", "estimated_cost_usd": 999.0,
                                         "recorded_at": NOW.isoformat()})
    assert runner.rollback_action("a1", "USER:op") is False
    assert _state(iid) == "stopped"


def test_rollback_allowed_while_kill_switch_on(aws, fake_db):
    iid, runner = _completed_stop(fake_db)
    runtime_config.emergency_stop()
    assert runner.rollback_action("a1", "USER:op") is True
    assert _state(iid) == "running"


def test_apply_tags_adds_only_missing_and_preserves_s3_tags(aws, fake_db):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="bkt")
    s3.put_bucket_tagging(Bucket="bkt", Tagging={"TagSet": [{"Key": "Project", "Value": "billing"}, {"Key": "Team", "Value": "x"}]})
    _seed(fake_db, "bkt", rtype="s3", action_type="apply_tags", tags={"Project": "billing", "Team": "x"})
    fake_db.rows("anomalies")[0]["anomaly_type"] = "untagged_resource"
    _live()
    assert ActionRunner().execute_action("a1") is True
    tags = {t["Key"]: t["Value"] for t in s3.get_bucket_tagging(Bucket="bkt")["TagSet"]}
    assert tags["Project"] == "billing"   # existing value not overwritten
    assert tags["Team"] == "x"            # unrelated tag not wiped by PutBucketTagging
    assert tags["Owner"] == "unassigned"  # missing required tag filled
    assert _action(fake_db)["status"] == "completed"
    assert fake_db.rows("anomalies")[0]["status"] == "resolved"
    assert fake_db.rows("resources")[0]["tags"]["Owner"] == "unassigned"
