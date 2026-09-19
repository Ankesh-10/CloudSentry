from abc import ABC, abstractmethod
from typing import List, Dict, Any

class CloudAdapter(ABC):
    """
    Abstract base class for cloud provider interactions.
    """
    
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
    def get_metric_data(self, queries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fetch metrics in batch from the cloud provider."""
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
