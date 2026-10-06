import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from backend.app.adapters.base import CloudAdapter, CloudProviderError
from backend.app.config import settings

logger = logging.getLogger(__name__)


def _tags_to_dict(tag_list) -> Dict[str, str]:
    if not tag_list:
        return {}
    return {t.get("Key"): t.get("Value") for t in tag_list if t.get("Key")}


class AWSAdapter(CloudAdapter):
    def __init__(self, region: Optional[str] = None):
        self.region = region or settings.AWS_DEFAULT_REGION
        boto_config = Config(
            region_name=self.region,
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        try:
            self.ec2 = boto3.client("ec2", config=boto_config)
            self.lambda_client = boto3.client("lambda", config=boto_config)
            self.s3 = boto3.client("s3", config=boto_config)
            self.rds = boto3.client("rds", config=boto_config)
            self.cloudwatch = boto3.client("cloudwatch", config=boto_config)
        except Exception as e:
            logger.error("Failed to initialize boto3 clients: %s", e)
            raise CloudProviderError(f"Failed to initialize AWS clients: {e}") from e

    def _call(self, op: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ClientError, BotoCoreError) as e:
            logger.error("AWS %s failed: %s", op, e)
            raise CloudProviderError(f"{op} failed") from e

    def discover_instances(self) -> List[Dict[str, Any]]:
        instances = []
        paginator = self._call("describe_instances", lambda: self.ec2.get_paginator("describe_instances"))
        try:
            for page in paginator.paginate():
                for reservation in page.get("Reservations", []):
                    for inst in reservation.get("Instances", []):
                        tags = _tags_to_dict(inst.get("Tags"))
                        az = inst.get("Placement", {}).get("AvailabilityZone", "")
                        instances.append({
                            "id": inst["InstanceId"],
                            "name": tags.get("Name", inst["InstanceId"]),
                            "region": az[:-1] if az else self.region,
                            "state": inst.get("State", {}).get("Name", "unknown"),
                            "tags": tags,
                            "metadata": {
                                "instance_type": inst.get("InstanceType"),
                                "launch_time": inst.get("LaunchTime").isoformat() if inst.get("LaunchTime") else None,
                                "raw_state": inst.get("State"),
                            },
                        })
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to discover EC2 instances: %s", e)
            raise CloudProviderError("discover_instances failed") from e
        return instances

    def discover_functions(self) -> List[Dict[str, Any]]:
        functions = []
        paginator = self._call("list_functions", lambda: self.lambda_client.get_paginator("list_functions"))
        try:
            for page in paginator.paginate():
                for fn in page.get("Functions", []):
                    fn_tags = self._lambda_tags(fn.get("FunctionArn"))
                    functions.append({
                        "id": fn["FunctionName"],
                        "name": fn["FunctionName"],
                        "region": self.region,
                        "state": "available",
                        "tags": fn_tags or {},
                        "metadata": {
                            "runtime": fn.get("Runtime"),
                            "memory": fn.get("MemorySize"),
                            "timeout": fn.get("Timeout"),
                            "arn": fn.get("FunctionArn"),
                            "last_modified": fn.get("LastModified"),
                            "tags_unreadable": fn_tags is None,
                        },
                    })
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to discover Lambda functions: %s", e)
            raise CloudProviderError("discover_functions failed") from e
        return functions

    def discover_buckets(self) -> List[Dict[str, Any]]:
        try:
            response = self.s3.list_buckets()
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to discover S3 buckets: %s", e)
            raise CloudProviderError("discover_buckets failed") from e

        buckets = []
        for b in response.get("Buckets", []):
            name = b["Name"]
            region = self.region
            try:
                loc = self.s3.get_bucket_location(Bucket=name)
                region = loc.get("LocationConstraint") or "us-east-1"
            except (ClientError, BotoCoreError):
                logger.warning("Could not get location for bucket %s", name)
            bucket_tags = self._bucket_tags(name)
            buckets.append({
                "id": name,
                "name": name,
                "region": region,
                "state": "available",
                "tags": bucket_tags or {},
                "metadata": {
                    "creation_date": b.get("CreationDate").isoformat() if b.get("CreationDate") else None,
                    "tags_unreadable": bucket_tags is None,
                },
            })
        return buckets

    def discover_databases(self) -> List[Dict[str, Any]]:
        dbs = []
        paginator = self._call("describe_db_instances", lambda: self.rds.get_paginator("describe_db_instances"))
        try:
            for page in paginator.paginate():
                for db in page.get("DBInstances", []):
                    dbs.append({
                        "id": db["DBInstanceIdentifier"],
                        "name": db["DBInstanceIdentifier"],
                        "region": self.region,
                        "state": db.get("DBInstanceStatus", "unknown"),
                        "tags": _tags_to_dict(db.get("TagList")),
                        "metadata": {
                            "instance_class": db.get("DBInstanceClass"),
                            "engine": db.get("Engine"),
                            "multi_az": db.get("MultiAZ"),
                            "arn": db.get("DBInstanceArn"),
                        },
                    })
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to discover RDS databases: %s", e)
            raise CloudProviderError("discover_databases failed") from e
        return dbs

    def discover_volumes(self) -> List[Dict[str, Any]]:
        volumes = []
        paginator = self._call("describe_volumes", lambda: self.ec2.get_paginator("describe_volumes"))
        try:
            for page in paginator.paginate():
                for vol in page.get("Volumes", []):
                    attachments = vol.get("Attachments") or []
                    volumes.append({
                        "id": vol["VolumeId"],
                        "name": vol["VolumeId"],
                        "region": self.region,
                        "state": vol.get("State", "unknown"),
                        "tags": _tags_to_dict(vol.get("Tags")),
                        "metadata": {
                            "size": vol.get("Size"),
                            "volume_type": vol.get("VolumeType"),
                            "attachments": attachments,
                            "unattached": vol.get("State") == "available" or not attachments,
                        },
                    })
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to discover EBS volumes: %s", e)
            raise CloudProviderError("discover_volumes failed") from e
        return volumes

    def get_metric_data(self, queries: List[Dict[str, Any]], start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        if not queries:
            return []
        results: List[Dict[str, Any]] = []
        try:
            for i in range(0, len(queries), 500):
                chunk = queries[i:i + 500]
                next_token = None
                while True:
                    kwargs = {
                        "MetricDataQueries": chunk,
                        "StartTime": start_time,
                        "EndTime": end_time,
                    }
                    if next_token:
                        kwargs["NextToken"] = next_token
                    response = self.cloudwatch.get_metric_data(**kwargs)
                    results.extend(response.get("MetricDataResults", []))
                    next_token = response.get("NextToken")
                    if not next_token:
                        break
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to fetch metric data: %s", e)
            raise CloudProviderError("get_metric_data failed") from e
        return results

    def stop_instance(self, instance_id: str) -> bool:
        try:
            self.ec2.stop_instances(InstanceIds=[instance_id])
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to stop instance %s: %s", instance_id, e)
            return False

    def start_instance(self, instance_id: str) -> bool:
        try:
            self.ec2.start_instances(InstanceIds=[instance_id])
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to start instance %s: %s", instance_id, e)
            return False

    def limit_function_concurrency(self, function_name: str, limit: int) -> bool:
        try:
            self.lambda_client.put_function_concurrency(
                FunctionName=function_name,
                ReservedConcurrentExecutions=limit,
            )
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to limit concurrency for %s: %s", function_name, e)
            return False

    def remove_function_concurrency(self, function_name: str) -> bool:
        try:
            self.lambda_client.delete_function_concurrency(FunctionName=function_name)
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to remove concurrency for %s: %s", function_name, e)
            return False

    def apply_tags(self, resource_id: str, tags: Dict[str, str], resource_type: str) -> bool:
        try:
            if resource_type in ("ec2", "ebs"):
                formatted = [{"Key": k, "Value": v} for k, v in tags.items()]
                self.ec2.create_tags(Resources=[resource_id], Tags=formatted)
            elif resource_type == "lambda":
                self.lambda_client.tag_resource(Resource=resource_id, Tags=tags)
            elif resource_type == "s3":
                # PutBucketTagging replaces the whole tag set: merge with existing tags.
                merged = {**(self._bucket_tags(resource_id, strict=True) or {}), **tags}
                tag_set = [{"Key": k, "Value": v} for k, v in merged.items()]
                self.s3.put_bucket_tagging(Bucket=resource_id, Tagging={"TagSet": tag_set})
            elif resource_type == "rds":
                formatted = [{"Key": k, "Value": v} for k, v in tags.items()]
                self.rds.add_tags_to_resource(ResourceName=resource_id, Tags=formatted)
            else:
                logger.error("Unsupported resource type for tagging: %s", resource_type)
                return False
            return True
        except (ClientError, BotoCoreError, CloudProviderError) as e:
            logger.error("Failed to apply tags to %s: %s", resource_id, e)
            return False

    def get_tags(self, resource_id: str, resource_type: str) -> Optional[Dict[str, str]]:
        """Live tags (None = could not read). resource_id is the instance /
        volume id, bucket name, or ARN for lambda and rds."""
        try:
            if resource_type == "ec2":
                resp = self.ec2.describe_instances(InstanceIds=[resource_id])
                return _tags_to_dict(resp["Reservations"][0]["Instances"][0].get("Tags"))
            if resource_type == "ebs":
                resp = self.ec2.describe_volumes(VolumeIds=[resource_id])
                return _tags_to_dict(resp["Volumes"][0].get("Tags"))
            if resource_type == "lambda":
                return self._lambda_tags(resource_id)
            if resource_type == "s3":
                return self._bucket_tags(resource_id, strict=True)
            if resource_type == "rds":
                resp = self.rds.describe_db_instances(Filters=[{"Name": "db-instance-id", "Values": [resource_id]}])
                dbs = resp.get("DBInstances") or []
                return _tags_to_dict(dbs[0].get("TagList")) if dbs else None
        except (ClientError, BotoCoreError, CloudProviderError, IndexError, KeyError) as e:
            logger.error("Failed to read tags for %s: %s", resource_id, e)
            return None
        logger.error("Unsupported resource type for tag read: %s", resource_type)
        return None

    def remove_tags(self, resource_id: str, keys: List[str], resource_type: str) -> bool:
        if not keys:
            return True
        try:
            if resource_type in ("ec2", "ebs"):
                self.ec2.delete_tags(Resources=[resource_id], Tags=[{"Key": k} for k in keys])
            elif resource_type == "lambda":
                self.lambda_client.untag_resource(Resource=resource_id, TagKeys=list(keys))
            elif resource_type == "s3":
                # No per-key delete for buckets: rewrite the set without the keys.
                remaining = {k: v for k, v in (self._bucket_tags(resource_id, strict=True) or {}).items()
                             if k not in set(keys)}
                if remaining:
                    tag_set = [{"Key": k, "Value": v} for k, v in remaining.items()]
                    self.s3.put_bucket_tagging(Bucket=resource_id, Tagging={"TagSet": tag_set})
                else:
                    self.s3.delete_bucket_tagging(Bucket=resource_id)
            elif resource_type == "rds":
                self.rds.remove_tags_from_resource(ResourceName=resource_id, TagKeys=list(keys))
            else:
                logger.error("Unsupported resource type for tag removal: %s", resource_type)
                return False
            return True
        except (ClientError, BotoCoreError, CloudProviderError) as e:
            logger.error("Failed to remove tags from %s: %s", resource_id, e)
            return False

    def _lambda_tags(self, arn: Optional[str]) -> Optional[Dict[str, str]]:
        """Returns None (not {}) when tags could not be read, so callers never
        mistake a permissions/API failure for an untagged resource."""
        if not arn:
            return None
        try:
            return dict(self.lambda_client.list_tags(Resource=arn).get("Tags") or {})
        except (ClientError, BotoCoreError) as e:
            logger.warning("Could not read tags for %s: %s", arn, e)
            return None

    def _bucket_tags(self, bucket: str, strict: bool = False) -> Optional[Dict[str, str]]:
        try:
            resp = self.s3.get_bucket_tagging(Bucket=bucket)
            return _tags_to_dict(resp.get("TagSet"))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchTagSet", "NoSuchTagSetError"):
                return {}
            logger.warning("Could not read tags for bucket %s: %s", bucket, e)
            if strict:
                raise CloudProviderError(f"get_bucket_tagging failed for {bucket}") from e
            return None
        except BotoCoreError as e:
            if strict:
                raise CloudProviderError(f"get_bucket_tagging failed for {bucket}") from e
            return None

    def get_instance_state(self, instance_id: str) -> Optional[str]:
        try:
            resp = self.ec2.describe_instances(InstanceIds=[instance_id])
            inst = resp["Reservations"][0]["Instances"][0]
            return inst.get("State", {}).get("Name")
        except (ClientError, BotoCoreError, IndexError, KeyError) as e:
            logger.error("Failed to describe instance %s: %s", instance_id, e)
            return None

    def get_function_concurrency(self, function_name: str) -> Optional[int]:
        try:
            resp = self.lambda_client.get_function_concurrency(FunctionName=function_name)
            return resp.get("ReservedConcurrentExecutions")
        except (ClientError, BotoCoreError) as e:
            logger.error("Failed to get concurrency for %s: %s", function_name, e)
            return None
