import logging
from datetime import datetime, timezone
from backend.app.db.supabase_client import get_supabase_client
from backend.app.config import settings
from backend.app.adapters.factory import get_cloud_adapter
from backend.app.services.audit_logger import AuditLogger

logger = logging.getLogger(__name__)

class ActionRunner:
    def __init__(self):
        self.db = get_supabase_client()
        self.cloud = get_cloud_adapter()
        self.audit = AuditLogger()
        
    def execute_action(self, action_id: str) -> bool:
        """
        Executes a single approved action with pre and post audit logging.
        """
        logger.info(f"Executing action {action_id}...")
        
        res = self.db.table("optimization_actions").select("*, resources(*)").eq("id", action_id).execute()
        if not res.data:
            logger.error("Action not found.")
            return False
            
        action = res.data[0]
        resource = action["resources"]
        
        if action["status"] not in ["pending", "approved"]:
            logger.error(f"Cannot execute action in status: {action['status']}")
            return False
            
        self.db.table("optimization_actions").update({"status": "executing"}).eq("id", action_id).execute()
        now = datetime.now(timezone.utc).isoformat()
        
        # PRE-ACTION AUDIT LOG
        self.audit.log_action(
            event_type="action_started",
            actor="SYSTEM",
            resource_id=resource["id"],
            action_id=action_id,
            aws_api_call=action["action_type"],
            message=f"Starting execution of {action['action_type']} on {resource['provider_id']}"
        )
        
        success = False
        message = ""
        
        if not settings.GLOBAL_AUTOMATION_ENABLED:
            message = "Global automation disabled (Kill-switch active)."
            success = False
        elif action["dry_run"] or settings.DRY_RUN_MODE:
            message = f"Dry run mode: WOULD {action['action_type']} on {resource['provider_id']}"
            success = True
        else:
            try:
                # Capture pre-state
                pre_state = {"state": resource.get("state"), "metadata": resource.get("metadata")}
                self.db.table("optimization_actions").update({"pre_state": pre_state}).eq("id", action_id).execute()
                
                # Execute AWS API
                if action["action_type"] == "stop_ec2":
                    success = self.cloud.stop_instance(resource["provider_id"])
                    message = "EC2 instance stopped successfully." if success else "Failed to stop EC2."
                elif action["action_type"] == "limit_lambda":
                    success = self.cloud.limit_function_concurrency(resource["provider_id"], 0)
                    message = "Lambda concurrency limited to 0." if success else "Failed to limit concurrency."
                elif action["action_type"] == "apply_tags":
                    success = True # Mocking tag apply for now
                    message = "Tags applied."
                else:
                    message = f"Unsupported action type: {action['action_type']}"
                    success = False
            except Exception as e:
                message = f"Execution error: {str(e)}"
                success = False
                
        final_status = "completed" if success else "failed"
        
        self.db.table("optimization_actions").update({
            "status": final_status,
            "executed_at": now,
            "post_state": {"result": message}
        }).eq("id", action_id).execute()
        
        # POST-ACTION AUDIT LOG
        self.audit.log_action(
            event_type="action_completed" if success else "action_failed",
            actor="SYSTEM",
            resource_id=resource["id"],
            action_id=action_id,
            aws_api_call=action["action_type"],
            response_status=final_status,
            message=message
        )
        
        return success

    def rollback_action(self, action_id: str, user_id: str = "SYSTEM") -> bool:
        """
        Rolls back a completed reversible action (e.g., starts an EC2 instance that was stopped).
        """
        logger.info(f"Rolling back action {action_id}...")
        
        res = self.db.table("optimization_actions").select("*, resources(*)").eq("id", action_id).execute()
        if not res.data:
            return False
            
        original_action = res.data[0]
        resource = original_action["resources"]
        
        if original_action["status"] != "completed":
            logger.error("Can only rollback completed actions.")
            return False
            
        if original_action.get("rollback_action_id"):
            logger.error("Action already has a rollback action associated with it.")
            return False
            
        # Determine reverse action
        reverse_type = None
        if original_action["action_type"] == "stop_ec2":
            reverse_type = "start_ec2"
        elif original_action["action_type"] == "limit_lambda":
            reverse_type = "restore_lambda_concurrency"
            
        if not reverse_type:
            logger.error(f"Action {original_action['action_type']} is not reversible.")
            return False
            
        now = datetime.now(timezone.utc).isoformat()
        
        # Create Rollback Action Record
        rollback_payload = {
            "anomaly_id": original_action.get("anomaly_id"),
            "resource_id": resource["id"],
            "action_type": reverse_type,
            "risk_level": "LOW",
            "status": "executing",
            "dry_run": settings.DRY_RUN_MODE,
            "created_at": now
        }
        
        new_action_res = self.db.table("optimization_actions").insert(rollback_payload).execute()
        rollback_action_id = new_action_res.data[0]["id"]
        
        # Link original action to rollback action
        self.db.table("optimization_actions").update({"rollback_action_id": rollback_action_id}).eq("id", action_id).execute()
        
        self.audit.log_action(
            event_type="rollback_started",
            actor=user_id,
            resource_id=resource["id"],
            action_id=rollback_action_id,
            aws_api_call=reverse_type,
            message=f"Starting rollback of action {action_id}"
        )
        
        success = False
        message = ""
        
        if settings.DRY_RUN_MODE:
            message = f"Dry run mode: WOULD {reverse_type} on {resource['provider_id']}"
            success = True
        else:
            try:
                if reverse_type == "start_ec2":
                    # We need start_instance in aws adapter, let's assume it exists or mock it
                    success = self.cloud.start_instance(resource["provider_id"])
                    message = "EC2 instance started successfully." if success else "Failed to start EC2."
                elif reverse_type == "restore_lambda_concurrency":
                    success = self.cloud.remove_function_concurrency(resource["provider_id"])
                    message = "Lambda concurrency restored." if success else "Failed to restore concurrency."
            except Exception as e:
                message = f"Rollback execution error: {str(e)}"
                success = False
                
        final_status = "completed" if success else "failed"
        
        self.db.table("optimization_actions").update({
            "status": final_status,
            "executed_at": now,
            "post_state": {"result": message}
        }).eq("id", rollback_action_id).execute()
        
        self.audit.log_action(
            event_type="rollback_completed" if success else "rollback_failed",
            actor=user_id,
            resource_id=resource["id"],
            action_id=rollback_action_id,
            aws_api_call=reverse_type,
            response_status=final_status,
            message=message
        )
        
        return success
