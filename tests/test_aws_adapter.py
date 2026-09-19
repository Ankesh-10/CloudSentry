import pytest
import os
import boto3
from moto import mock_aws
from backend.app.adapters.aws import AWSAdapter

# Set fake credentials for Moto before any boto3 client is created
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

@pytest.fixture
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    pass

@pytest.fixture
def aws_adapter(aws_credentials):
    with mock_aws():
        # Setup mock environment
        ec2 = boto3.client("ec2", region_name="us-east-1")
        # Create a mock instance
        ec2.run_instances(
            ImageId="ami-12c6146b",
            MinCount=1,
            MaxCount=1,
            InstanceType="t2.micro",
            TagSpecifications=[
                {'ResourceType': 'instance', 'Tags': [{'Key': 'Name', 'Value': 'TestInstance'}]}
            ]
        )
        
        adapter = AWSAdapter()
        yield adapter

def test_discover_instances(aws_adapter):
    instances = aws_adapter.discover_instances()
    assert len(instances) == 1
    assert instances[0]["InstanceType"] == "t2.micro"
    
def test_stop_instance(aws_adapter):
    instances = aws_adapter.discover_instances()
    instance_id = instances[0]["InstanceId"]
    
    # Instance starts in 'running' state
    assert instances[0]["State"]["Name"] == "running"
    
    # Stop it via adapter
    success = aws_adapter.stop_instance(instance_id)
    assert success is True
    
    # Verify it is stopped
    updated_instances = aws_adapter.discover_instances()
    assert updated_instances[0]["State"]["Name"] == "stopped"
