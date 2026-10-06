"""GCP Compute Engine adapter.

Supported: discovering VM instances across every zone of GCP_PROJECT_ID,
stopping/starting them, and reading their live state (so the action runner's
pre-flight check and post-action verification work).

Not supported yet (the base class defaults return "unsupported"): Cloud
Functions, buckets, Cloud SQL, persistent disks, Cloud Monitoring telemetry and
labels. Without telemetry, metric-based detection (idle VMs) does not run for
GCP; only metadata rules do. See README "Cloud providers".
"""
import concurrent.futures
import logging
from datetime import datetime
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


def _state(status: Optional[str]) -> str:
    return STATE_MAP.get((status or "").upper(), "unknown")


def split_instance_id(provider_id: str) -> Tuple[str, str]:
    """Instances are identified as '<zone>/<name>' (names are only unique per zone)."""
    zone, sep, name = (provider_id or "").partition("/")
    if not sep or not zone or not name:
        raise CloudProviderError(f"Invalid GCP instance id {provider_id!r}; expected '<zone>/<name>'")
    return zone, name


class GCPAdapter(CloudAdapter):
    def __init__(self, client=None, project_id: Optional[str] = None):
        self.project_id = project_id or settings.GCP_PROJECT_ID
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

    def get_metric_data(self, queries: List[Dict[str, Any]], start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        # Telemetry queries are CloudWatch-shaped; Cloud Monitoring is not wired up.
        return []

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
