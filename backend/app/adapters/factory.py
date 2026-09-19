from backend.app.config import settings
from backend.app.adapters.base import CloudAdapter

def get_cloud_adapter() -> CloudAdapter:
    """
    Factory function to return the correct CloudAdapter based on configuration.
    """
    provider = settings.CLOUD_PROVIDER.lower()
    
    if provider == "aws":
        from backend.app.adapters.aws import AWSAdapter
        return AWSAdapter()
    elif provider == "gcp":
        from backend.app.adapters.gcp import GCPAdapter
        return GCPAdapter()
    else:
        raise ValueError(f"Unsupported CLOUD_PROVIDER: {provider}")
