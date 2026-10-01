import pytest
import os
import boto3
from moto import mock_aws
from backend.app.adapters.aws import AWSAdapter

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def aws_adapter():
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        ec2.run_instances(
            ImageId="ami-12c6146b",
            MinCount=1,
            MaxCount=1,
            InstanceType="t2.micro",
            TagSpecifications=[
                {"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "TestInstance"}]}
            ],
        )
        yield AWSAdapter()


def test_discover_instances(aws_adapter):
    instances = aws_adapter.discover_instances()
    assert len(instances) == 1
    assert instances[0]["metadata"]["instance_type"] == "t2.micro"
    assert instances[0]["state"] == "running"


def test_stop_instance(aws_adapter):
    instances = aws_adapter.discover_instances()
    instance_id = instances[0]["id"]
    assert instances[0]["state"] == "running"
    success = aws_adapter.stop_instance(instance_id)
    assert success is True
    updated = aws_adapter.discover_instances()
    assert updated[0]["state"] == "stopped"
