"""GCP adapter (fake Compute client) and multi-region AWS (moto)."""
import concurrent.futures
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from backend.app.adapters.base import CloudProviderError
from backend.app.adapters.gcp import GCPAdapter, split_instance_id
from backend.app.config import settings


# -- GCP -------------------------------------------------------------------------------

class _Op:
    def __init__(self, exc=None):
        self.exc = exc

    def result(self, timeout=None):
        if self.exc:
            raise self.exc


class _FakeCompute:
    def __init__(self):
        self.instances = {
            ("us-central1-a", "web-1"): "RUNNING",
            ("europe-west1-b", "batch-1"): "TERMINATED",   # GCE's word for "stopped"
        }
        self.calls = []
        self.op_exc = None

    def aggregated_list(self, project):
        by_zone = {}
        for (zone, name), status in self.instances.items():
            by_zone.setdefault(zone, []).append(SimpleNamespace(
                name=name, status=status, labels={"team": "core"},
                machine_type=f"zones/{zone}/machineTypes/e2-small"))
        yield "zones/asia-east1-a", SimpleNamespace(instances=[])   # empty zone
        for zone, insts in by_zone.items():
            yield f"zones/{zone}", SimpleNamespace(instances=insts)

    def get(self, project, zone, instance):
        return SimpleNamespace(status=self.instances[(zone, instance)])

    def stop(self, project, zone, instance):
        self.calls.append(("stop", zone, instance))
        self.instances[(zone, instance)] = "TERMINATED"
        return _Op(self.op_exc)

    def start(self, project, zone, instance):
        self.calls.append(("start", zone, instance))
        self.instances[(zone, instance)] = "RUNNING"
        return _Op(self.op_exc)


@pytest.fixture
def gcp():
    client = _FakeCompute()
    return GCPAdapter(client=client, project_id="proj"), client


def test_gcp_discovers_all_zones_with_zone_qualified_ids(gcp):
    adapter, _ = gcp
    found = {i["id"]: i for i in adapter.discover_instances()}
    assert set(found) == {"us-central1-a/web-1", "europe-west1-b/batch-1"}
    assert found["us-central1-a/web-1"]["region"] == "us-central1"
    assert found["us-central1-a/web-1"]["metadata"]["instance_type"] == "e2-small"


def test_gcp_terminated_means_stopped_not_deleted(gcp):
    adapter, _ = gcp
    found = {i["id"]: i for i in adapter.discover_instances()}
    assert found["europe-west1-b/batch-1"]["state"] == "stopped"
    assert adapter.get_instance_state("europe-west1-b/batch-1") == "stopped"


def test_gcp_stop_start_target_the_right_zone_and_are_verifiable(gcp):
    adapter, client = gcp
    assert adapter.stop_instance("us-central1-a/web-1") is True
    assert client.calls == [("stop", "us-central1-a", "web-1")]
    assert adapter.get_instance_state("us-central1-a/web-1") == "stopped"   # matches VERIFY_TARGETS
    assert adapter.start_instance("us-central1-a/web-1") is True
    assert adapter.get_instance_state("us-central1-a/web-1") == "running"


def test_gcp_operation_timeout_defers_to_verification_but_errors_fail(gcp):
    adapter, client = gcp
    client.op_exc = concurrent.futures.TimeoutError()
    assert adapter.stop_instance("us-central1-a/web-1") is True
    client.op_exc = RuntimeError("quota exceeded")
    assert adapter.start_instance("us-central1-a/web-1") is False


@pytest.mark.parametrize("bad", ["web-1", "/web-1", "zone/", ""])
def test_gcp_rejects_unqualified_ids(gcp, bad):
    adapter, _ = gcp
    with pytest.raises(CloudProviderError):
        split_instance_id(bad)
    assert adapter.stop_instance(bad) is False
    assert adapter.get_instance_state(bad) is None


def test_gcp_requires_project(monkeypatch):
    monkeypatch.setattr(settings, "GCP_PROJECT_ID", "")
    with pytest.raises(CloudProviderError):
        GCPAdapter()


# -- GCP telemetry (fake Cloud Monitoring client) -------------------------------------------

CPU = "compute.googleapis.com/instance/cpu/utilization"
NET_IN = "compute.googleapis.com/instance/network/received_bytes_count"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _series(metric_type, zone, name, points):
    """points: [(aligned end_time, value)]; CPU is a double, byte counts int64."""
    is_double = metric_type == CPU
    return SimpleNamespace(
        metric=SimpleNamespace(type=metric_type, labels={"instance_name": name}),
        resource=SimpleNamespace(type="gce_instance", labels={"zone": zone, "instance_id": "123"}),
        points=[SimpleNamespace(
            interval=SimpleNamespace(start_time=t, end_time=t),
            value=SimpleNamespace(double_value=v if is_double else 0.0,
                                  int64_value=0 if is_double else v))
            for t, v in points],
    )


class _FakeMonitoring:
    def __init__(self, series=(), exc=None):
        self.series = list(series)
        self.exc = exc
        self.requests = []

    def list_time_series(self, request):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return [s for s in self.series if f'metric.type = "{s.metric.type}"' in request["filter"]]


def _ec2_query(qid, provider_id, metric, stat="Average", period=300):
    return {"Id": qid, "ReturnData": True, "MetricStat": {
        "Metric": {"Namespace": "AWS/EC2", "MetricName": metric,
                   "Dimensions": [{"Name": "InstanceId", "Value": provider_id}]},
        "Period": period, "Stat": stat}}


def _gcp_with(monitoring):
    return GCPAdapter(client=_FakeCompute(), project_id="proj", monitoring_client=monitoring)


def test_gcp_metrics_are_translated_to_detector_names_and_units():
    mon = _FakeMonitoring([
        _series(CPU, "us-central1-a", "web-1", [(T0 + timedelta(minutes=5), 0.42)]),
        _series(NET_IN, "us-central1-a", "web-1", [(T0 + timedelta(minutes=5), 12_000)]),
        # Same name in another zone is a different VM and was not asked for.
        _series(CPU, "europe-west1-b", "web-1", [(T0 + timedelta(minutes=5), 0.99)]),
    ])
    queries = [_ec2_query("q_0", "us-central1-a/web-1", "CPUUtilization"),
               _ec2_query("q_1", "us-central1-a/web-1", "NetworkIn"),
               _ec2_query("q_2", "us-central1-a/web-1", "NetworkOut")]
    results = {r["Id"]: r for r in _gcp_with(mon).get_metric_data(queries, T0 - timedelta(minutes=30), T0 + timedelta(minutes=10))}

    # GCP reports CPU as a 0-1 fraction; the detector thresholds are percent.
    assert results["q_0"]["Values"] == [pytest.approx(42.0)]
    assert results["q_1"]["Values"] == [12_000.0]
    assert results["q_2"]["Values"] == []                     # no data is not zero usage
    # Timestamps label the start of the period, as CloudWatch does.
    assert results["q_0"]["Timestamps"] == [T0]

    req = next(r for r in mon.requests if CPU in r["filter"])
    assert req["name"] == "projects/proj"
    assert 'resource.type = "gce_instance"' in req["filter"] and '"web-1"' in req["filter"]
    assert req["aggregation"]["alignment_period"] == {"seconds": 300}


def test_gcp_metrics_sum_byte_counts_and_average_cpu():
    from google.cloud import monitoring_v3
    mon = _FakeMonitoring()
    queries = [_ec2_query("q_0", "us-central1-a/web-1", "CPUUtilization"),
               _ec2_query("q_1", "us-central1-a/web-1", "NetworkIn")]
    _gcp_with(mon).get_metric_data(queries, T0, T0 + timedelta(minutes=30))
    aligners = {CPU if CPU in r["filter"] else NET_IN: r["aggregation"]["per_series_aligner"] for r in mon.requests}
    assert aligners[CPU] == monitoring_v3.Aggregation.Aligner.ALIGN_MEAN
    # Byte counts are per-minute deltas: their sum is the bytes moved in the period.
    assert aligners[NET_IN] == monitoring_v3.Aggregation.Aligner.ALIGN_SUM


def test_gcp_metrics_skip_unsupported_queries_without_calling_the_api():
    mon = _FakeMonitoring()
    lam = {"Id": "q_9", "MetricStat": {"Metric": {"Namespace": "AWS/Lambda", "MetricName": "Invocations",
                                                   "Dimensions": [{"Name": "FunctionName", "Value": "f"}]},
                                        "Period": 300, "Stat": "Sum"}}
    bad_id = _ec2_query("q_1", "not-zone-qualified", "CPUUtilization")
    assert _gcp_with(mon).get_metric_data([lam, bad_id], T0, T0 + timedelta(minutes=30)) == []
    assert mon.requests == []


def test_gcp_metrics_api_failure_raises_provider_error():
    mon = _FakeMonitoring(exc=RuntimeError("403 permission denied"))
    with pytest.raises(CloudProviderError):
        _gcp_with(mon).get_metric_data([_ec2_query("q_0", "us-central1-a/web-1", "CPUUtilization")],
                                       T0, T0 + timedelta(minutes=30))


def test_telemetry_collects_gcp_vm_samples(fake_db, monkeypatch):
    from backend.app.services import telemetry
    mon = _FakeMonitoring([_series(CPU, "us-central1-a", "web-1", [(T0 + timedelta(minutes=5), 0.1)])])
    monkeypatch.setattr(telemetry, "get_cloud_adapter", lambda region=None: _gcp_with(mon))
    monkeypatch.setattr(telemetry, "_cw_calls_this_hour", {"hour": None, "count": 0, "metrics": 0})
    fake_db.table("resources").insert({"id": "r1", "provider_id": "us-central1-a/web-1", "resource_type": "ec2",
                                       "region": "us-central1", "state": "running"}).execute()

    records = telemetry.TelemetryService()._collect()

    assert records == [(T0, "r1", "CPUUtilization", pytest.approx(10.0), "Percent")]


def test_gcp_metrics_answer_every_query_for_the_same_vm():
    # Two resource rows for one VM produce two queries with one (zone, name).
    mon = _FakeMonitoring([_series(CPU, "us-central1-a", "web-1", [(T0 + timedelta(minutes=5), 0.5)])])
    queries = [_ec2_query("q_0", "us-central1-a/web-1", "CPUUtilization"),
               _ec2_query("q_1", "us-central1-a/web-1", "CPUUtilization")]
    results = {r["Id"]: r for r in _gcp_with(mon).get_metric_data(queries, T0, T0 + timedelta(minutes=30))}
    assert results["q_0"]["Values"] == results["q_1"]["Values"] == [pytest.approx(50.0)]
    assert len(mon.requests) == 1


def test_gcp_metrics_missing_package_is_a_provider_error(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_monitoring(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "google.cloud" and "monitoring_v3" in (fromlist or ()):
            raise ImportError("No module named 'google.cloud.monitoring_v3'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_monitoring)
    # Even with a client injected, the request types come from the package.
    with pytest.raises(CloudProviderError):
        _gcp_with(_FakeMonitoring()).get_metric_data(
            [_ec2_query("q_0", "us-central1-a/web-1", "CPUUtilization")], T0, T0 + timedelta(minutes=30))


def test_gcp_metric_call_count_is_requests_not_cloudwatch_chunks():
    from backend.app.adapters import gcp
    adapter = _gcp_with(_FakeMonitoring())
    names = [f"us-central1-a/vm-{i}" for i in range(gcp.FILTER_CHUNK + 1)]
    queries = [_ec2_query(f"q_{m}_{i}", n, m) for m in ("CPUUtilization", "NetworkIn", "NetworkOut")
               for i, n in enumerate(names)]
    # 3 metrics x 2 name chunks, though CloudWatch would take them in one call.
    assert adapter.metric_call_count(queries) == 6
    lam = {"Id": "q_9", "MetricStat": {"Metric": {"Namespace": "AWS/Lambda", "MetricName": "Invocations",
                                                   "Dimensions": [{"Name": "FunctionName", "Value": "f"}]},
                                        "Period": 300, "Stat": "Sum"}}
    assert adapter.metric_call_count([lam]) == 0


def test_telemetry_charges_the_cap_with_gcp_request_count(fake_db, monkeypatch):
    from backend.app.adapters import gcp
    from backend.app.services import telemetry
    mon = _FakeMonitoring()
    monkeypatch.setattr(telemetry, "get_cloud_adapter", lambda region=None: _gcp_with(mon))
    counter = {"hour": None, "count": 0, "metrics": 0}
    monkeypatch.setattr(telemetry, "_cw_calls_this_hour", counter)
    for i in range(gcp.FILTER_CHUNK + 1):
        fake_db.table("resources").insert({"id": f"r{i}", "provider_id": f"us-central1-a/vm-{i}",
                                           "resource_type": "ec2", "region": "us-central1",
                                           "state": "running"}).execute()

    telemetry.TelemetryService()._collect()

    assert counter["count"] == len(mon.requests) == 6


# -- multi-region AWS ---------------------------------------------------------------------

@pytest.fixture
def aws():
    with mock_aws():
        yield


def _run_instance(region):
    ec2 = boto3.client("ec2", region_name=region)
    return ec2.run_instances(ImageId="ami-12c6146b", MinCount=1, MaxCount=1,
                             InstanceType="t2.micro")["Instances"][0]["InstanceId"]


def test_region_list_defaults_and_dedupes(monkeypatch):
    monkeypatch.setattr(settings, "AWS_REGIONS", "eu-west-1, us-east-1 ,eu-west-1")
    assert settings.aws_region_list() == ["us-east-1", "eu-west-1"]
    monkeypatch.setattr(settings, "AWS_REGIONS", "")
    assert settings.aws_region_list() == ["us-east-1"]


def test_discovery_covers_every_configured_region(aws, fake_db, monkeypatch):
    from backend.app.services.discovery import DiscoveryService
    monkeypatch.setattr(settings, "AWS_REGIONS", "eu-west-1")
    us, eu = _run_instance("us-east-1"), _run_instance("eu-west-1")
    DiscoveryService().run()
    regions = {r["provider_id"]: r["region"] for r in fake_db.rows("resources") if r["resource_type"] == "ec2"}
    assert regions == {us: "us-east-1", eu: "eu-west-1"}


def _function(region, name="api"):
    import io
    import zipfile
    iam = boto3.client("iam", region_name="us-east-1")
    try:
        role = iam.create_role(RoleName="r", AssumeRolePolicyDocument="{}")["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName="r")["Role"]["Arn"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("h.py", "def h(e, c): return 1")
    boto3.client("lambda", region_name=region).create_function(
        FunctionName=name, Runtime="python3.11", Role=role, Handler="h.h", Code={"ZipFile": buf.getvalue()})


def test_same_function_name_in_two_regions_is_two_resources(aws, fake_db, monkeypatch):
    """Lambda names are only unique per region; neither copy may be dropped,
    and a second run must update, not duplicate or delete, them."""
    from backend.app.services.discovery import DiscoveryService
    monkeypatch.setattr(settings, "AWS_REGIONS", "eu-west-1")
    _function("us-east-1")
    _function("eu-west-1")
    DiscoveryService().run()
    DiscoveryService().run()
    rows = [r for r in fake_db.rows("resources") if r["resource_type"] == "lambda"]
    assert sorted((r["region"], r["provider_id"], r["state"]) for r in rows) == [
        ("eu-west-1", "api", "available"), ("us-east-1", "api", "available")]


def test_failed_region_does_not_mark_other_resources_deleted(aws, fake_db, monkeypatch):
    from backend.app.adapters.aws import AWSAdapter
    from backend.app.services.discovery import DiscoveryError, DiscoveryService
    monkeypatch.setattr(settings, "AWS_REGIONS", "eu-west-1")
    _run_instance("us-east-1")
    eu = _run_instance("eu-west-1")
    DiscoveryService().run()

    real = AWSAdapter.discover_instances

    def flaky(self):
        if self.region == "eu-west-1":
            raise CloudProviderError("throttled")
        return real(self)

    monkeypatch.setattr(AWSAdapter, "discover_instances", flaky)
    with pytest.raises(DiscoveryError):
        DiscoveryService().run()
    eu_row = next(r for r in fake_db.rows("resources") if r["provider_id"] == eu)
    assert eu_row["state"] == "running"


def test_account_is_keyed_by_provider_and_account_id(aws, fake_db, monkeypatch):
    from backend.app.services.discovery import DiscoveryService
    fake_db.rows("cloud_accounts").append({"id": "other", "provider": "aws", "account_id": "111111111111",
                                           "region": "us-east-1"})
    monkeypatch.setattr(settings, "CLOUD_ACCOUNT_ID", "222222222222")
    _run_instance("us-east-1")
    DiscoveryService().run()
    account_ids = {r["account_id"] for r in fake_db.rows("resources")}
    assert "other" not in account_ids and len(fake_db.rows("cloud_accounts")) == 2


def test_actions_use_the_resource_region(aws, fake_db):
    from backend.app.services import runtime_config
    from backend.app.services.action_runner import ActionRunner
    iid = _run_instance("eu-west-1")
    fake_db.rows("resources").append({"id": "r1", "provider_id": iid, "resource_type": "ec2", "region": "eu-west-1",
                                      "state": "running", "tags": {}, "protected": False, "metadata": {}})
    fake_db.rows("optimization_actions").append({
        "id": "a1", "resource_id": "r1", "action_type": "stop_ec2", "risk_level": "MEDIUM", "status": "pending",
        "requires_approval": False, "dry_run": True, "created_at": datetime.now(timezone.utc).isoformat()})
    runtime_config.set_flag("GLOBAL_AUTOMATION_ENABLED", True, persist=False)
    runtime_config.set_flag("DRY_RUN_MODE", False, persist=False)
    assert ActionRunner().execute_action("a1") is True
    state = boto3.client("ec2", region_name="eu-west-1").describe_instances(InstanceIds=[iid])
    assert state["Reservations"][0]["Instances"][0]["State"]["Name"] == "stopped"


def test_cost_estimates_use_regional_pricing(fake_db):
    from backend.app.services.cost_estimator import CostEstimationService
    est = CostEstimationService()
    r = {"resource_type": "ec2", "state": "running", "metadata": {"instance_type": "t3.micro"}}
    us = est.estimate_hourly({**r, "region": "us-east-1"})
    mumbai = est.estimate_hourly({**r, "region": "ap-south-1"})
    assert us != mumbai and mumbai == est._pricing_for("ap-south-1")["ec2"]["t3.micro"]
