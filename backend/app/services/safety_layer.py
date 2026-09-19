import logging
from datetime import datetime, timedelta, timezone
from backend.app.db.supabase_client import get_supabase_client
from backend.app.config import settings

logger = logging.getLogger(__name__)

class SafetyLayer:
    def __init__(self):
        self.db = get_supabase_client()

    def check_action_safe(self, action_type: str, resource_id: str) -> tuple[bool, str]:
        """
        Evaluates safety constraints before proposing or executing an action.
        Returns: (is_safe: bool, reason: str)
        """
        # 1. Kill-Switch Check
        if not settings.GLOBAL_AUTOMATION_ENABLED:
            return False, "GLOBAL_AUTOMATION_ENABLED is False (Kill-switch activated)."

        # 2. Protected Resource Check
        res = self.db.table("resources").select("protected, tags").eq("id", resource_id).execute()
        if not res.data:
            return False, "Resource not found in database."
            
        resource = res.data[0]
        if resource.get("protected") is True:
            return False, "Resource is explicitly marked as protected."
            
        tags = resource.get("tags") or {}
        if tags.get("cloudsentry:protected") == "true":
            return False, "Resource has 'cloudsentry:protected=true' tag."

        # 3. Daily Action Limit Check
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        actions_today = self.db.table("optimization_actions") \
            .select("id", count="exact") \
            .gte("created_at", start_of_day.isoformat()) \
            .eq("dry_run", False) \
            .execute()
            
        count = actions_today.count or 0
        if count >= settings.MAX_ACTIONS_PER_DAY:
            return False, f"Daily action limit reached ({count} >= {settings.MAX_ACTIONS_PER_DAY})."

        # 4. Per-Resource Cooldown Check
        cooldown_window = now - timedelta(minutes=settings.ACTION_COOLDOWN_MINUTES)
        recent_actions = self.db.table("optimization_actions") \
            .select("id") \
            .eq("resource_id", resource_id) \
            .gte("created_at", cooldown_window.isoformat()) \
            .execute()
            
        if recent_actions.data:
            return False, f"Resource is in cooldown period (last {settings.ACTION_COOLDOWN_MINUTES} mins)."

        return True, "Passed all safety checks."
