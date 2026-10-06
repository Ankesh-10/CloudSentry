import io
import zipfile
from datetime import timedelta

import boto3
import pytest
from moto import mock_aws

from backend.app.services.discovery import DiscoveryService
from backend.app.services.telemetry import WINDOWS, build_queries


@pytest.fixture
def aws():
    with mock_aws():
        yield


def _make_lambda(name):
    iam = boto3.client("iam", region_name="us-east-1")
    role = iam.create_role(RoleName=f"role-{name}", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("h.py", "def handler(e, c): return 1")
    boto3.client("lambda", region_name="us-east-1").create_function(
        FunctionName=name, Runtime="python3.11", Role=role, Handler="h.handler", Code={"ZipFile": buf.getvalue()},
        Tags={"Project": "p"})


def test_discovery_all_types_tags_and_name_collision(aws, fake_db):
    ec2 = boto3.client("ec2", region_name="us-east-1")
    iid = ec2.run_instances(ImageId="ami-12c6146b", MinCount=1, MaxCount=1, InstanceType="t2.micro")["Instances"][0]["InstanceId"]
    ec2.create_volume(AvailabilityZone="us-east-1a", Size=8)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="shared-name")
    s3.put_bucket_tagging(Bucket="shared-name", Tagging={"TagSet": [{"Key": "Owner", "Value": "me"}]})
    _make_lambda("shared-name")  # same identifier as the bucket

    summary = DiscoveryService().run()
    assert summary["errors"] == []
    by_type = {}
    for r in fake_db.rows("resources"):
        by_type.setdefault(r["resource_type"], []).append(r)
    assert set(by_type) >= {"ec2", "s3", "lambda", "ebs"}
    # A Lambda and a bucket sharing a name are two rows, not one overwritten row.
    assert by_type["s3"][0]["provider_id"] == by_type["lambda"][0]["provider_id"] == "shared-name"
    assert by_type["s3"][0]["tags"] == {"Owner": "me"}
    assert by_type["lambda"][0]["tags"] == {"Project": "p"}
    assert by_type["lambda"][0]["metadata"]["arn"].endswith(":function:shared-name")
    assert all(r.get("first_seen") for r in fake_db.rows("resources"))

    # Second cycle: existing rows are updated in place, first_seen preserved.
    first_seen = {r["id"]: r["first_seen"] for r in fake_db.rows("resources")}
    fake_db.rows("resources")[0]["protected"] = True
    DiscoveryService().run()
    assert {r["id"]: r["first_seen"] for r in fake_db.rows("resources")} == first_seen
    assert fake_db.rows("resources")[0]["protected"] is True

    # Terminated + aged-out instances are marked deleted, not left "running".
    ec2.terminate_instances(InstanceIds=[iid])
    inst = next(r for r in fake_db.rows("resources") if r["provider_id"] == iid)
    DiscoveryService().run()
    assert inst["state"] in ("terminated", "deleted")


def test_cloudwatch_cap_counts_billed_metrics_not_just_calls(monkeypatch):
    """GetMetricData is billed per metric: one call carrying 500 metrics costs
    500x a call carrying one, so the cost cap must count metrics."""
    from backend.app.services import runtime_config, telemetry
    monkeypatch.setattr(telemetry, "_cw_calls_this_hour", {"hour": None, "count": 0, "metrics": 0})
    runtime_config.set_flag("MAX_CW_METRICS_PER_HOUR", 1000, persist=False)
    assert telemetry._note_cw_calls(1, 600) is True
    assert telemetry._note_cw_calls(1, 600) is False      # 1 call, but over the metric budget
    assert (telemetry._cw_calls_this_hour["count"], telemetry._cw_calls_this_hour["metrics"]) == (1, 600)
    assert telemetry._note_cw_calls(1, 400) is True


def test_build_queries_uses_per_type_windows():
    resources = [
        {"id": "a", "provider_id": "i-1", "resource_type": "ec2"},
        {"id": "b", "provider_id": "bkt", "resource_type": "s3"},
        {"id": "c", "provider_id": "x", "resource_type": "unknown"},
    ]
    groups = build_queries(resources)
    ec2_q, ec2_map = groups[WINDOWS["ec2"]]
    s3_q, _ = groups[WINDOWS["s3"]]
    assert len(ec2_q) == 3 and len(s3_q) == 2
    # Lookback must exceed CloudWatch publication delay (old code used 5 minutes).
    assert WINDOWS["ec2"][1] >= timedelta(minutes=15)
    assert WINDOWS["s3"][0] == 86400 and WINDOWS["s3"][1] >= timedelta(days=2)
    dims = {d["Name"]: d["Value"] for d in s3_q[0]["MetricStat"]["Metric"]["Dimensions"]}
    assert dims["BucketName"] == "bkt" and "StorageType" in dims
    ids = [q["Id"] for g in groups.values() for q in g[0]]
    assert len(ids) == len(set(ids))
