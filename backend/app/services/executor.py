import logging
from datetime import datetime, timezone
from backend.app.db.supabase_client import get_supabase_client
from backend.app.config import settings
from backend.app.adapters.aws import AWSAdapter

logger = logging.getLogger(__name__)

class ExecutorService:
    def __init__(self):
        self.db = get_supabase_client()
        self.cloud = AWSAdapter()
        
    def execute_action(self, action_id: str) -> bool:
        """
        Executes a single approved action.
        """
        logger.info(f"Executing action {action_id}...")
        
        # 1. Fetch action and resource
        res = self.db.table("optimization_actions").select("*, resources(*)").eq("id", action_id).execute()
        if not res.data:
            logger.error("Action not found.")
            return False
            
        action = res.data[0]
        resource = action["resources"]
        
        if action["status"] != "approved":
            logger.error(f"Cannot execute action in status: {action['status']}")
            return False
            
        # Update to executing
        self.db.table("optimization_actions").update({"status": "executing"}).eq("id", action_id).execute()
        now = datetime.now(timezone.utc).isoformat()
        
        success = False
        message = ""
        
        # 2. Safety gates
        if not settings.GLOBAL_AUTOMATION_ENABLED:
            message = "Global automation is disabled. Simulation only."
            success = True
        elif action["dry_run"] or settings.DRY_RUN_MODE:
            message = "Dry run mode enabled. Simulating execution."
            success = True
        else:
            # 3. Actual execution
            try:
                if action["action_type"] == "stop_ec2":
                    success = self.cloud.stop_instance(resource["provider_id"])
                    message = "EC2 instance stopped successfully." if success else "Failed to stop EC2 instance."
                elif action["action_type"] == "limit_lambda":
                    success = self.cloud.limit_function_concurrency(resource["provider_id"], 0)
                    message = "Lambda concurrency limited." if success else "Failed to limit Lambda concurrency."
                else:
                    message = f"Unsupported action type: {action['action_type']}"
                    success = False
            except Exception as e:
                message = f"Execution error: {str(e)}"
                success = False
                
        # 4. Post-execution status update
        final_status = "completed" if success else "failed"
        
        self.db.table("optimization_actions").update({
            "status": final_status,
            "executed_at": now,
            "post_state": {"result": message}
        }).eq("id", action_id).execute()
        
        # 5. Audit Log
        self.db.table("audit_logs").insert({
            "event_type": "action_execution",
            "actor": "SYSTEM",
            "resource_id": resource["id"],
            "action_id": action_id,
            "aws_api_call": action["action_type"],
            "response_status": final_status,
            "message": message,
            "created_at": now
        }).execute()
        
        return success
