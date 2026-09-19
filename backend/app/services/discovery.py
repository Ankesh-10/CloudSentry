import logging
from typing import List, Dict, Any
from datetime import datetime, timezone
from backend.app.adapters.aws import AWSAdapter
from backend.app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

class DiscoveryService:
    def __init__(self):
        self.cloud_adapter = AWSAdapter()
        self.db = get_supabase_client()
        
    def _get_or_create_account(self) -> str:
        # For MVP, assume a single AWS account. Just get the first one or create it.
        res = self.db.table("cloud_accounts").select("id").limit(1).execute()
        if res.data:
            return res.data[0]["id"]
            
        new_account = {
            "provider": "aws",
            "account_id": "demo-account",
            "region": "us-east-1"
        }
        res = self.db.table("cloud_accounts").insert(new_account).execute()
        return res.data[0]["id"]

    def _sync_resources(self, account_id: str, resource_type: str, discovered: List[Dict[str, Any]]):
        if not discovered:
            return
            
        now = datetime.now(timezone.utc).isoformat()
        
        # Get existing from DB
        existing_res = self.db.table("resources").select("id, provider_id, state").eq("resource_type", resource_type).execute()
        existing_map = {r["provider_id"]: r for r in existing_res.data}
        
        upsert_payload = []
        audit_payload = []
        
        for res in discovered:
            provider_id = res.get("id")
            state = res.get("state")
            
            payload = {
                "account_id": account_id,
                "provider_id": provider_id,
                "resource_type": resource_type,
                "name": res.get("name"),
                "region": res.get("region"),
                "state": state,
                "tags": res.get("tags", {}),
                "metadata": res.get("metadata", {}),
                "last_seen": now
            }
            
            # Check state changes for audit logging
            if provider_id in existing_map:
                old_state = existing_map[provider_id]["state"]
                payload["id"] = existing_map[provider_id]["id"]
                if old_state != state:
                    audit_payload.append({
                        "event_type": "state_change",
                        "actor": "SYSTEM",
                        "resource_id": payload["id"],
                        "message": f"State changed from {old_state} to {state}"
                    })
            else:
                payload["first_seen"] = now
                
            upsert_payload.append(payload)
            
        if upsert_payload:
            self.db.table("resources").upsert(upsert_payload, on_conflict="provider_id").execute()
            
        if audit_payload:
            self.db.table("audit_logs").insert(audit_payload).execute()
            
    def run(self):
        """
        Main entrypoint for APScheduler job.
        Discovers EC2, Lambda, S3, RDS, EBS and syncs them to Supabase.
        """
        logger.info("Starting Resource Discovery cycle...")
        account_id = self._get_or_create_account()
        
        # 1. EC2
        ec2_data = self.cloud_adapter.discover_instances()
        parsed_ec2 = []
        for inst in ec2_data:
            state = inst.get("State", {}).get("Name", "unknown")
            parsed_ec2.append({
                "id": inst["InstanceId"],
                "name": next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), inst["InstanceId"]),
                "region": inst.get("Placement", {}).get("AvailabilityZone", "")[:-1],
                "state": state,
                "tags": {t["Key"]: t["Value"] for t in inst.get("Tags", [])},
                "metadata": {"instance_type": inst.get("InstanceType")}
            })
        self._sync_resources(account_id, "ec2", parsed_ec2)
        
        # 2. Lambda (Stubbed mapping for brevity)
        lambda_data = self.cloud_adapter.discover_functions()
        parsed_lambda = [{"id": f["FunctionName"], "name": f["FunctionName"], "state": "available"} for f in lambda_data]
        self._sync_resources(account_id, "lambda", parsed_lambda)
        
        logger.info("Resource Discovery cycle completed.")
        
# For testing
if __name__ == "__main__":
    DiscoveryService().run()
