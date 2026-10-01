import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.app.adapters.base import CloudAdapter, CloudProviderError
from backend.app.config import settings

logger = logging.getLogger(__name__)


class GCPAdapter(CloudAdapter):
    def __init__(self):
        self.project_id = settings.GCP_PROJECT_ID
        self.zone = "us-central1-a"
        self.compute_client = None
        if not self.project_id:
            logger.warning("GCP_PROJECT_ID not set. GCP Adapter will not function properly.")
            return
        try:
            from google.cloud import compute_v1
            self.compute_client = compute_v1.InstancesClient()
        except ImportError:
            logger.error("Google Cloud SDKs not installed.")
        except Exception as e:
            logger.error("Failed to initialize GCP clients: %s", e)
            raise CloudProviderError("Failed to initialize GCP clients") from e

    def discover_instances(self) -> List[Dict[str, Any]]:
        if not self.compute_client:
            raise CloudProviderError("GCP compute client is not initialized")
        instances = []
        try:
            from google.cloud import compute_v1
            request = compute_v1.ListInstancesRequest(project=self.project_id, zone=self.zone)
            for instance in self.compute_client.list(request=request):
                instances.append({
                    "id": instance.name,
                    "name": instance.name,
                    "region": self.zone,
                    "state": (instance.status or "unknown").lower(),
                    "tags": dict(instance.labels) if getattr(instance, "labels", None) else {},
                    "metadata": {"instance_type": instance.machine_type.split("/")[-1]},
                })
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
        return []

    def stop_instance(self, instance_id: str) -> bool:
        if not self.compute_client:
            return False
        try:
            from google.cloud import compute_v1
            request = compute_v1.StopInstanceRequest(
                project=self.project_id, zone=self.zone, instance=instance_id
            )
            operation = self.compute_client.stop(request=request)
            operation.result()
            return True
        except Exception as e:
            logger.error("Failed to stop GCP instance %s: %s", instance_id, e)
            return False

    def start_instance(self, instance_id: str) -> bool:
        if not self.compute_client:
            return False
        try:
            from google.cloud import compute_v1
            request = compute_v1.StartInstanceRequest(
                project=self.project_id, zone=self.zone, instance=instance_id
            )
            operation = self.compute_client.start(request=request)
            operation.result()
            return True
        except Exception as e:
            logger.error("Failed to start GCP instance %s: %s", instance_id, e)
            return False

    def limit_function_concurrency(self, function_name: str, limit: int) -> bool:
        return False

    def remove_function_concurrency(self, function_name: str) -> bool:
        return False

    def apply_tags(self, resource_id: str, tags: Dict[str, str], resource_type: str) -> bool:
        return False

    def get_instance_state(self, instance_id: str) -> Optional[str]:
        return None

    def get_function_concurrency(self, function_name: str) -> Optional[int]:
        return None
