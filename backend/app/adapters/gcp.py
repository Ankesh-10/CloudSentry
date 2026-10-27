"""GCP Compute Engine adapter.

Supported: discovering VM instances across every zone of GCP_PROJECT_ID,
stopping/starting them, reading their live state (so the action runner's
pre-flight check and post-action verification work), and VM telemetry from
Cloud Monitoring (CPU, network in/out), translated to the CloudWatch metric
names and units the detector uses.

Not supported yet (the base class defaults return "unsupported"): Cloud
Functions, buckets, Cloud SQL, persistent disks and labels. See README
"Cloud providers".
"""
import concurrent.futures
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.app.adapters.base import CloudAdapter, CloudProviderError
from backend.app.config import settings

logger = logging.getLogger(__name__)

# GCE status -> the provider-neutral states the rest of the app uses. GCE calls
# a stopped VM "TERMINATED"; reporting that as "terminated" made discovery and
# detection treat stopped VMs as deleted, and verification never saw "stopped".
STATE_MAP = {
    "PROVISIONING": "pending",
    "STAGING": "pending",
    "RUNNING": "running",
    "STOPPING": "stopping",
    "STOPPED": "stopped",
    "TERMINATED": "stopped",
    "SUSPENDING": "stopping",
    "SUSPENDED": "stopped",
    "REPAIRING": "pending",
}
OPERATION_TIMEOUT_SECONDS = 120

# CloudWatch metric name the detector uses -> (Cloud Monitoring metric type,
# aligner, scale). CPU utilization is a 0-1 gauge (the detector works in
# percent); byte counts are per-minute deltas, so their sum over the period is
# the bytes moved in it, like CloudWatch's basic-monitoring NetworkIn/Out.
METRIC_TRANSLATION = {
    "CPUUtilization": ("compute.googleapis.com/instance/cpu/utilization", "ALIGN_MEAN", 100.0),
    "NetworkIn": ("compute.googleapis.com/instance/network/received_bytes_count", "ALIGN_SUM", 1.0),
    "NetworkOut": ("compute.googleapis.com/instance/network/sent_bytes_count", "ALIGN_SUM", 1.0),
}
# Instance names per list_time_series filter (filters have a length limit).
FILTER_CHUNK = 100


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _monitoring_v3():
    # Imported lazily so discovery and actions work without the package; a
    # missing package is a provider error, which telemetry skips, not a crash.
    try:
        from google.cloud import monitoring_v3
    except ImportError as e:
        raise CloudProviderError("google-cloud-monitoring is not installed") from e
    return monitoring_v3


def _as_utc(ts) -> datetime:
    # proto-plus returns DatetimeWithNanoseconds; store plain aware datetimes.
    return datetime.fromtimestamp(ts.timestamp(), tz=timezone.utc)


def _state(status: Optional[str]) -> str:
    return STATE_MAP.get((status or "").upper(), "unknown")


def split_instance_id(provider_id: str) -> Tuple[str, str]:
    """Instances are identified as '<zone>/<name>' (names are only unique per zone)."""
    zone, sep, name = (provider_id or "").partition("/")
    if not sep or not zone or not name:
        raise CloudProviderError(f"Invalid GCP instance id {provider_id!r}; expected '<zone>/<name>'")
    return zone, name


class GCPAdapter(CloudAdapter):
    def __init__(self, client=None, project_id: Optional[str] = None, monitoring_client=None):
        self.project_id = project_id or settings.GCP_PROJECT_ID
        # Built on first use, so discovery and actions work without it.
        self.monitoring_client = monitoring_client
        self.compute_client = client
        if self.compute_client is not None:
            return
        if not self.project_id:
            raise CloudProviderError("GCP_PROJECT_ID is not set")
        try:
            from google.cloud import compute_v1
            self.compute_client = compute_v1.InstancesClient()
        except ImportError as e:
            raise CloudProviderError("google-cloud-compute is not installed") from e
        except Exception as e:
            logger.error("Failed to initialize GCP clients: %s", type(e).__name__)
            raise CloudProviderError("Failed to initialize GCP clients") from e

    # -- discovery -------------------------------------------------------------

    def discover_instances(self) -> List[Dict[str, Any]]:
        instances = []
        try:
            for scope, scoped in self.compute_client.aggregated_list(project=self.project_id):
                zone = scope.rsplit("/", 1)[-1]  # "zones/us-central1-a"
                for inst in getattr(scoped, "instances", None) or []:
                    instances.append({
                        "id": f"{zone}/{inst.name}",
                        "name": inst.name,
                        "region": zone.rsplit("-", 1)[0],
                        "state": _state(inst.status),
                        "tags": dict(inst.labels) if getattr(inst, "labels", None) else {},
                        "metadata": {
                            "instance_type": (inst.machine_type or "").split("/")[-1],
                            "zone": zone,
                        },
                    })
        except CloudProviderError:
            raise
        except Exception as e:
            logger.error("Failed to discover GCP Compute instances: %s", e)
            raise CloudProviderError("discover_instances failed") from e
        return instances

    def discover_functions(self) -> List[Dict[str, Any]]:
        return []

    def discover_buckets(self) -> List[Dict[str, Any]]:
        return []

    def discover_databases(self) -> List[Dict[str, Any]]:
        return []

    def discover_volumes(self) -> List[Dict[str, Any]]:
        return []

    def _monitoring(self):
        if self.monitoring_client is None:
            monitoring_v3 = _monitoring_v3()
            try:
                self.monitoring_client = monitoring_v3.MetricServiceClient()
            except Exception as e:
                logger.error("Failed to initialize GCP Monitoring client: %s", type(e).__name__)
                raise CloudProviderError("Failed to initialize GCP Monitoring client") from e
        return self.monitoring_client

    @staticmethod
    def _group_queries(queries: List[Dict[str, Any]]) -> Dict[Tuple[str, int], Dict[Tuple[str, str], List[str]]]:
        """(metric_name, period) -> {(zone, name): query ids}. Unsupported
        queries are left out. Several queries may ask for the same VM (e.g.
        duplicate resource rows); each gets the series."""
        wanted: Dict[Tuple[str, int], Dict[Tuple[str, str], List[str]]] = {}
        for q in queries:
            stat = q.get("MetricStat") or {}
            metric = stat.get("Metric") or {}
            if metric.get("Namespace") != "AWS/EC2" or metric.get("MetricName") not in METRIC_TRANSLATION:
                continue
            dims = {d["Name"]: d["Value"] for d in metric.get("Dimensions") or []}
            try:
                key = split_instance_id(dims.get("InstanceId", ""))
            except CloudProviderError:
                continue
            group = wanted.setdefault((metric["MetricName"], int(stat.get("Period", 300))), {})
            group.setdefault(key, []).append(q["Id"])
        return wanted

    def metric_call_count(self, queries: List[Dict[str, Any]]) -> int:
        # One list_time_series request per metric/period per FILTER_CHUNK names.
        return sum(-(-len({name for _, name in ids}) // FILTER_CHUNK)
                   for ids in self._group_queries(queries).values())

    def get_metric_data(self, queries: List[Dict[str, Any]], start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """Answer the telemetry service's CloudWatch-shaped queries from Cloud
        Monitoring. Only VM metrics in METRIC_TRANSLATION are supported; other
        queries get no result (no data, which is not zero usage)."""
        wanted = self._group_queries(queries)
        if not wanted:
            return []

        monitoring_v3 = _monitoring_v3()
        client = self._monitoring()
        points: Dict[str, List[Tuple[datetime, float]]] = {}
        try:
            for (metric_name, period), ids in wanted.items():
                metric_type, aligner, scale = METRIC_TRANSLATION[metric_name]
                names = sorted({name for _, name in ids})
                for i in range(0, len(names), FILTER_CHUNK):
                    name_list = ", ".join(_quote(n) for n in names[i:i + FILTER_CHUNK])
                    series_iter = client.list_time_series(request={
                        "name": f"projects/{self.project_id}",
                        "filter": (f'metric.type = "{metric_type}" AND resource.type = "gce_instance" '
                                   f"AND metric.labels.instance_name = one_of({name_list})"),
                        "interval": {"start_time": start_time, "end_time": end_time},
                        "aggregation": {"alignment_period": {"seconds": period},
                                        "per_series_aligner": getattr(monitoring_v3.Aggregation.Aligner, aligner)},
                        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
                    })
                    for series in series_iter:
                        key = (series.resource.labels.get("zone"), series.metric.labels.get("instance_name"))
                        qids = ids.get(key)
                        if not qids:
                            continue
                        samples = []
                        for p in series.points:
                            value = p.value.double_value or float(p.value.int64_value)
                            # Aligned points are stamped at the end of their period;
                            # CloudWatch (and the stored samples) use the start.
                            start = _as_utc(p.interval.end_time) - timedelta(seconds=period)
                            samples.append((start, value * scale))
                        for qid in qids:
                            points.setdefault(qid, []).extend(samples)
        except Exception as e:
            logger.error("Failed to fetch GCP metric data: %s", e)
            raise CloudProviderError("get_metric_data failed") from e

        results = []
        for ids in wanted.values():
            for qid in (qid for qids in ids.values() for qid in qids):
                samples = sorted(points.get(qid, []))
                results.append({"Id": qid, "Timestamps": [t for t, _ in samples], "Values": [v for _, v in samples]})
        return results

    # -- actions -----------------------------------------------------------------

    def _wait(self, operation, what: str) -> bool:
        try:
            operation.result(timeout=OPERATION_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            # Submitted but not confirmed yet: the action runner's verification
            # decides the outcome from the observed state.
            logger.warning("GCP %s not confirmed within %ss", what, OPERATION_TIMEOUT_SECONDS)
        except Exception as e:
            logger.error("GCP %s failed: %s", what, e)
            return False
        return True

    def stop_instance(self, instance_id: str) -> bool:
        try:
            zone, name = split_instance_id(instance_id)
            op = self.compute_client.stop(project=self.project_id, zone=zone, instance=name)
        except Exception as e:
            logger.error("Failed to stop GCP instance %s: %s", instance_id, e)
            return False
        return self._wait(op, f"stop of {instance_id}")

    def start_instance(self, instance_id: str) -> bool:
        try:
            zone, name = split_instance_id(instance_id)
            op = self.compute_client.start(project=self.project_id, zone=zone, instance=name)
        except Exception as e:
            logger.error("Failed to start GCP instance %s: %s", instance_id, e)
            return False
        return self._wait(op, f"start of {instance_id}")

    def get_instance_state(self, instance_id: str) -> Optional[str]:
        try:
            zone, name = split_instance_id(instance_id)
            inst = self.compute_client.get(project=self.project_id, zone=zone, instance=name)
        except Exception as e:
            logger.error("Failed to read GCP instance %s: %s", instance_id, e)
            return None
        return _state(inst.status)

    def limit_function_concurrency(self, function_name: str, limit: int) -> bool:
        return False

    def remove_function_concurrency(self, function_name: str) -> bool:
        return False

    def apply_tags(self, resource_id: str, tags: Dict[str, str], resource_type: str) -> bool:
        # GCP labels are lowercase-only and need a fingerprint round-trip; the
        # required-tag rules are written for AWS tag keys. Not supported yet.
        return False

    def get_function_concurrency(self, function_name: str) -> Optional[int]:
        return None
