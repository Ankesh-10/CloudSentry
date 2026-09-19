import os
import pytest
import boto3
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from moto import mock_aws

# Set mock AWS credentials before importing app modules
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["GLOBAL_AUTOMATION_ENABLED"] = "true"
os.environ["DRY_RUN_MODE"] = "false"

from backend.app.main import app
from backend.app.services.discovery import DiscoveryService
from backend.app.services.telemetry import TelemetryService
from backend.app.services.anomaly_detector import AnomalyDetectorService
from backend.app.services.policy_engine import PolicyEngine
from backend.app.services.action_runner import ActionRunner
from backend.app.config import settings

@pytest.fixture(scope="module")
def test_client():
    with TestClient(app) as client:
        yield client

@pytest.fixture
def mock_cloud_env():
    with mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        cloudwatch = boto3.client("cloudwatch", region_name="us-east-1")
        
        # Create a mock instance
        res = ec2.run_instances(
            ImageId="ami-12c6146b",
            MinCount=1,
            MaxCount=1,
            InstanceType="t2.micro",
            TagSpecifications=[
                {'ResourceType': 'instance', 'Tags': [{'Key': 'Name', 'Value': 'IntegrationTestInstance'}]}
            ]
        )
        instance_id = res['Instances'][0]['InstanceId']
        
        # Seed CloudWatch with an anomaly (Huge CPU spike)
        now = datetime.now(timezone.utc)
        cloudwatch.put_metric_data(
            Namespace="AWS/EC2",
            MetricData=[
                {
                    "MetricName": "CPUUtilization",
                    "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                    "Timestamp": now - timedelta(minutes=5),
                    "Value": 99.9, # Anomaly level CPU
                    "Unit": "Percent"
                }
            ]
        )
        yield {"instance_id": instance_id}

@pytest.fixture
def mock_db():
    """
    Provides a comprehensive mocked database that fakes Supabase chains
    so the pipeline can run without actual credentials.
    """
    class MockExecute:
        def __init__(self, data=None, count=0):
            self.data = data or []
            self.count = count

    class MockQuery:
        def __init__(self, table_name, db_state):
            self.table_name = table_name
            self.db_state = db_state
            
        def select(self, *args, **kwargs): return self
        def insert(self, payload):
            if isinstance(payload, dict): payload = [payload]
            for p in payload:
                if 'id' not in p: p['id'] = f"mock-{self.table_name}-id"
                self.db_state[self.table_name].append(p)
            self.last_payload = payload
            return self
        def upsert(self, payload, *args, **kwargs):
            return self.insert(payload)
        def update(self, payload):
            if self.db_state[self.table_name]:
                self.db_state[self.table_name][0].update(payload)
            self.last_payload = self.db_state[self.table_name]
            return self
        def eq(self, *args): return self
        def neq(self, *args): return self
        def gte(self, *args): return self
        def in_(self, *args): return self
        def limit(self, *args): return self
        def order(self, *args, **kwargs): return self
        
        def execute(self):
            # Special case for specific tables to return joined relations
            if self.table_name == "anomalies" and self.db_state[self.table_name]:
                for a in self.db_state[self.table_name]:
                    a["resources"] = self.db_state["resources"][0] if self.db_state["resources"] else {"id": "mock-res", "resource_type": "ec2", "provider_id": "i-mock", "state": "running", "protected": False}
            elif self.table_name == "optimization_actions" and self.db_state[self.table_name]:
                for a in self.db_state[self.table_name]:
                    a["resources"] = self.db_state["resources"][0] if self.db_state["resources"] else {"id": "mock-res", "resource_type": "ec2", "provider_id": "i-mock", "state": "running", "protected": False}
                    
            if hasattr(self, 'last_payload'):
                ret = self.last_payload
                delattr(self, 'last_payload')
                return MockExecute(data=ret, count=len(ret))
            
            return MockExecute(data=self.db_state[self.table_name], count=len(self.db_state[self.table_name]))

    class MockClient:
        def __init__(self):
            self.db_state = {
                "cloud_accounts": [{"id": "mock-acc-id"}],
                "resources": [],
                "resource_metrics": [],
                "anomalies": [],
                "policies": [{
                    "id": "mock-policy-id",
                    "name": "Integration Test Stop Policy",
                    "enabled": True,
                    "resource_type": "ec2",
                    "anomaly_type": "idle_compute",
                    "conditions": {"field": "anomaly.score", "op": "gt", "value": 0.5},
                    "action_type": "stop_ec2",
                    "risk_level": "LOW",
                    "requires_approval": False
                }],
                "optimization_actions": [],
                "audit_logs": []
            }
        
        def table(self, name):
            return MockQuery(name, self.db_state)

    return MockClient()

@pytest.mark.asyncio
@patch("backend.app.services.discovery.get_supabase_client")
@patch("backend.app.services.telemetry.get_supabase_client")
@patch("backend.app.services.anomaly_detector.get_supabase_client")
@patch("backend.app.services.policy_engine.get_supabase_client")
@patch("backend.app.services.safety_layer.get_supabase_client")
@patch("backend.app.services.action_runner.get_supabase_client")
@patch("backend.app.services.audit_logger.get_supabase_client")
@patch("backend.app.api.actions.db")
@patch("backend.app.api.actions.runner.db")
async def test_end_to_end_pipeline(
    mock_runner_db, mock_api_db,
    mock_audit_db, mock_action_db, mock_safety_db, mock_policy_db, 
    mock_anomaly_db, mock_telemetry_db, mock_discovery_db, 
    test_client, mock_cloud_env, mock_db
):
    """
    Simulates the entire APScheduler pipeline running sequentially using Moto and a Mocked DB.
    """
    # Inject our mock database into all services
    for mock_target in [mock_runner_db, mock_api_db, mock_audit_db, mock_action_db, mock_safety_db, mock_policy_db, mock_anomaly_db, mock_telemetry_db, mock_discovery_db]:
        mock_target.return_value = mock_db
        
    mock_api_db.table = mock_db.table # FastApi instance

    instance_id = mock_cloud_env["instance_id"]
    
    settings.GLOBAL_AUTOMATION_ENABLED = True
    settings.DRY_RUN_MODE = False

    # 1. Discovery
    discovery = DiscoveryService()
    discovery.run()
    
    assert len(mock_db.db_state["resources"]) > 0
    assert mock_db.db_state["resources"][0]["provider_id"] == instance_id
    
    # Update mock DB resource to have the correct mock ID
    db_resource_id = mock_db.db_state["resources"][0]["id"]

    # 2. Telemetry Collection
    # To avoid asyncpg complexity, we patch get_pool and mock the connection
    with patch("backend.app.services.telemetry.get_pool") as mock_get_pool:
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        mock_get_pool.return_value = mock_pool
        
        telemetry = TelemetryService()
        await telemetry.run() # Ensure to await the async run method
        
        # Verify that copy_records_to_table was called
        assert mock_conn.copy_records_to_table.called
        
        # Manually seed the DB state since we intercepted the db write
        mock_db.db_state["resource_metrics"].append({
            "resource_id": db_resource_id,
            "metric_name": "CPUUtilization",
            "value": 99.9,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        # Add missing DB default fields to our mock resource
        mock_db.db_state["resources"][0]["protected"] = False

    # 3. Anomaly Detection
    # Since Z-Score needs 500 historical points, the real detector will just return.
    # We will manually trigger the anomaly creation to simulate a detected anomaly.
    with patch("backend.app.services.anomaly_detector.AnomalyDetectorService.run") as mock_detector:
        mock_db.db_state["anomalies"].append({
            "id": "mock-anomaly-id",
            "resource_id": db_resource_id,
            "anomaly_type": "idle_compute",
            "anomaly_score": 0.99,
            "confidence": 0.9,
            "status": "active",
            "features_snapshot": {"rolling_avg_cpu_24h": 0.5}
        })
        detector = AnomalyDetectorService()
        # If it's async, we should await it (scheduler might wrap it, but direct call needs await if async)
        if asyncio.iscoroutinefunction(detector.run) or isinstance(mock_detector, AsyncMock):
            await detector.run()
        else:
            detector.run()

    # 4. Policy Engine
    policy_engine = PolicyEngine()
    policy_engine.evaluate_all()
    
    assert len(mock_db.db_state["optimization_actions"]) > 0
    action_id = mock_db.db_state["optimization_actions"][0]["id"]
    assert mock_db.db_state["optimization_actions"][0]["action_type"] == "stop_ec2"
    
    # 5. API Approval
    # The policy engine creates the action as 'pending'
    response = test_client.post(f"/api/v1/actions/{action_id}/approve", json={"approved": True, "user_id": "TEST_USER"})
    assert response.status_code == 200
    assert mock_db.db_state["optimization_actions"][0]["status"] == "approved"
    
    # 6. Action Execution (Triggered via API background task, but we will call it manually to test the service)
    runner = ActionRunner()
    success = runner.execute_action(action_id)
    assert success is True
    
    # Verify EC2 was stopped in mock AWS
    ec2 = boto3.client("ec2", region_name="us-east-1")
    inst_state = ec2.describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]['State']['Name']
    assert inst_state == 'stopped'
    
    # Verify Audit Logs
    assert len(mock_db.db_state["audit_logs"]) >= 2
    assert mock_db.db_state["audit_logs"][0]["event_type"] == "action_started"
    assert mock_db.db_state["audit_logs"][1]["event_type"] == "action_completed"
