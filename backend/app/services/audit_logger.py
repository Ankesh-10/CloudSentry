import logging
from datetime import datetime, timezone
from backend.app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

class AuditLogger:
    def __init__(self):
        self.db = get_supabase_client()

    def log_action(self, event_type: str, actor: str, resource_id: str, 
                   action_id: str = None, aws_api_call: str = None, 
                   request_params: dict = None, response_status: str = None, 
                   message: str = None):
        """
        Writes an audit record to the database. 
        Supports both pre-action (intended) and post-action (result) logging.
        """
        now = datetime.now(timezone.utc).isoformat()
        
        payload = {
            "event_type": event_type,
            "actor": actor,
            "resource_id": resource_id,
            "created_at": now
        }
        
        if action_id: payload["action_id"] = action_id
        if aws_api_call: payload["aws_api_call"] = aws_api_call
        if request_params: payload["request_params"] = request_params
        if response_status: payload["response_status"] = response_status
        if message: payload["message"] = message
            
        try:
            self.db.table("audit_logs").insert(payload).execute()
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
