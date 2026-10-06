from typing import Optional

from backend.app.adapters.base import CloudAdapter
from backend.app.config import settings


def get_cloud_adapter(region: Optional[str] = None) -> CloudAdapter:
    """
    Return the CloudAdapter for the configured provider.

    `region` selects an AWS region (default: AWS_DEFAULT_REGION). GCP adapters
    are project-wide (instances carry their zone), so it is ignored there.
    """
    provider = settings.CLOUD_PROVIDER.lower()

    if provider == "aws":
        from backend.app.adapters.aws import AWSAdapter
        return AWSAdapter(region=region)
    elif provider == "gcp":
        from backend.app.adapters.gcp import GCPAdapter
        return GCPAdapter()
    else:
        raise ValueError(f"Unsupported CLOUD_PROVIDER: {provider}")
