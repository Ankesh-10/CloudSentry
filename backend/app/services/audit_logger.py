import logging
from datetime import datetime, timezone

from backend.app.db.supabase_client import get_supabase_client
from backend.app.request_context import current_client_ip, current_request_id

logger = logging.getLogger(__name__)


class AuditWriteError(RuntimeError):
    """A required audit record could not be written; the audited change must
    not proceed."""


class AuditLogger:
    def __init__(self):
        self.db = get_supabase_client()

    def log_action(
        self,
        event_type: str,
        actor: str,
        resource_id: str = None,
        action_id: str = None,
        aws_api_call: str = None,
        request_params: dict = None,
        response_status: str = None,
        message: str = None,
        required: bool = False,
    ) -> bool:
        """Insert one audit row. Returns False on failure, or raises
        AuditWriteError when required=True (write the audit row *before* the
        change it records, so a change can never happen unaudited)."""
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "event_type": event_type,
            "actor": actor,
            "created_at": now,
        }
        if resource_id:
            payload["resource_id"] = resource_id
        if action_id:
            payload["action_id"] = action_id
        if aws_api_call:
            payload["aws_api_call"] = aws_api_call
        if request_params:
            payload["request_params"] = request_params
        if response_status:
            payload["response_status"] = response_status
        if message:
            payload["message"] = message
        request_id = current_request_id()
        if request_id:
            payload["request_id"] = request_id
        client_ip = current_client_ip()
        if client_ip:
            payload["client_ip"] = client_ip

        try:
            self.db.table("audit_logs").insert(payload).execute()
            return True
        except Exception as e:
            logger.error("Failed to write audit log (event=%s actor=%s): %s", event_type, actor, e)
            if required:
                raise AuditWriteError(f"Audit write failed for {event_type}") from e
            return False
