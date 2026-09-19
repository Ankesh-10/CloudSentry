import boto3
import logging
from botocore.config import Config
from botocore.exceptions import ClientError, BotoCoreError
from typing import List, Dict, Any
from backend.app.config import settings
from backend.app.adapters.base import CloudAdapter

logger = logging.getLogger(__name__)

class AWSAdapter(CloudAdapter):
    """
    AWS implementation of the CloudAdapter using boto3.
    """
    def __init__(self):
        # Configure retry strategy as defined in the Tech Stack
        boto_config = Config(
            region_name=settings.AWS_DEFAULT_REGION,
            retries={'max_attempts': 3, 'mode': 'adaptive'}
        )
        
        # In a real environment, boto3 will automatically pick up AWS_ACCESS_KEY_ID 
        # and AWS_SECRET_ACCESS_KEY from the environment if they are set.
        try:
            self.ec2 = boto3.client('ec2', config=boto_config)
            self.lambda_client = boto3.client('lambda', config=boto_config)
            self.s3 = boto3.client('s3', config=boto_config)
            self.rds = boto3.client('rds', config=boto_config)
            self.cloudwatch = boto3.client('cloudwatch', config=boto_config)
        except Exception as e:
            logger.error(f"Failed to initialize boto3 clients: {e}")

    def discover_instances(self) -> List[Dict[str, Any]]:
        instances = []
        try:
            paginator = self.ec2.get_paginator('describe_instances')
            for page in paginator.paginate():
                for reservation in page.get('Reservations', []):
                    for inst in reservation.get('Instances', []):
                        instances.append(inst)
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to discover EC2 instances: {e}")
        return instances
        
    def discover_functions(self) -> List[Dict[str, Any]]:
        functions = []
        try:
            paginator = self.lambda_client.get_paginator('list_functions')
            for page in paginator.paginate():
                functions.extend(page.get('Functions', []))
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to discover Lambda functions: {e}")
        return functions
        
    def discover_buckets(self) -> List[Dict[str, Any]]:
        buckets = []
        try:
            response = self.s3.list_buckets()
            buckets = response.get('Buckets', [])
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to discover S3 buckets: {e}")
        return buckets
        
    def discover_databases(self) -> List[Dict[str, Any]]:
        dbs = []
        try:
            paginator = self.rds.get_paginator('describe_db_instances')
            for page in paginator.paginate():
                dbs.extend(page.get('DBInstances', []))
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to discover RDS databases: {e}")
        return dbs
        
    def discover_volumes(self) -> List[Dict[str, Any]]:
        volumes = []
        try:
            paginator = self.ec2.get_paginator('describe_volumes')
            for page in paginator.paginate():
                volumes.extend(page.get('Volumes', []))
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to discover EBS volumes: {e}")
        return volumes
        
    def get_metric_data(self, queries: List[Dict[str, Any]], start_time, end_time) -> List[Dict[str, Any]]:
        """Fetch metrics in batch from CloudWatch."""
        results = []
        try:
            # Note: A single call can handle up to 500 MetricDataQueries. 
            # If queries > 500, we would need to batch them here.
            if not queries:
                return []
                
            response = self.cloudwatch.get_metric_data(
                MetricDataQueries=queries,
                StartTime=start_time,
                EndTime=end_time
            )
            results = response.get('MetricDataResults', [])
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to fetch metric data: {e}")
        return results
        
    def stop_instance(self, instance_id: str) -> bool:
        try:
            self.ec2.stop_instances(InstanceIds=[instance_id])
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to stop instance {instance_id}: {e}")
            return False
            
    def start_instance(self, instance_id: str) -> bool:
        try:
            self.ec2.start_instances(InstanceIds=[instance_id])
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to start instance {instance_id}: {e}")
            return False
            
    def limit_function_concurrency(self, function_name: str, limit: int) -> bool:
        try:
            self.lambda_client.put_function_concurrency(
                FunctionName=function_name,
                ReservedConcurrentExecutions=limit
            )
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to limit concurrency for {function_name}: {e}")
            return False
            
    def remove_function_concurrency(self, function_name: str) -> bool:
        try:
            self.lambda_client.delete_function_concurrency(FunctionName=function_name)
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to remove concurrency for {function_name}: {e}")
            return False

    def apply_tags(self, resource_id: str, tags: Dict[str, str], resource_type: str) -> bool:
        try:
            if resource_type in ('ec2', 'ebs'):
                formatted_tags = [{'Key': k, 'Value': v} for k, v in tags.items()]
                self.ec2.create_tags(Resources=[resource_id], Tags=formatted_tags)
            elif resource_type == 'lambda':
                self.lambda_client.tag_resource(Resource=resource_id, Tags=tags) # Note: requires ARN for lambda
            # More resource types as needed...
            return True
        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to apply tags to {resource_id}: {e}")
            return False
