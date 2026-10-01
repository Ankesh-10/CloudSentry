from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


class CloudProviderError(Exception):
    """Raised when a cloud API call fails. Distinct from an empty result set."""


class CloudAdapter(ABC):
    @abstractmethod
    def discover_instances(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def discover_functions(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def discover_buckets(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def discover_databases(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def discover_volumes(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_metric_data(
        self,
        queries: List[Dict[str, Any]],
        start_time: datetime,
        end_time: datetime,
    ) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def stop_instance(self, instance_id: str) -> bool:
        pass

    @abstractmethod
    def start_instance(self, instance_id: str) -> bool:
        pass

    @abstractmethod
    def limit_function_concurrency(self, function_name: str, limit: int) -> bool:
        pass

    @abstractmethod
    def remove_function_concurrency(self, function_name: str) -> bool:
        pass

    @abstractmethod
    def apply_tags(self, resource_id: str, tags: Dict[str, str], resource_type: str) -> bool:
        pass

    def get_tags(self, resource_id: str, resource_type: str) -> Optional[Dict[str, str]]:
        """Current tags, or None if they could not be read. Default: unsupported."""
        return None

    def remove_tags(self, resource_id: str, keys: List[str], resource_type: str) -> bool:
        """Remove only the given tag keys. Default: unsupported."""
        return False

    @abstractmethod
    def get_instance_state(self, instance_id: str) -> Optional[str]:
        pass

    @abstractmethod
    def get_function_concurrency(self, function_name: str) -> Optional[int]:
        pass
