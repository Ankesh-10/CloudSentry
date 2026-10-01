import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.app.config import settings
from backend.app.db.pagination import fetch_all
from backend.app.db.supabase_client import get_supabase_client
from backend.app.services import runtime_config

logger = logging.getLogger(__name__)

DESTRUCTIVE_ACTIONS = {"delete_ebs_volume", "delete_volume", "terminate_ec2", "delete_bucket", "delete_db_instance"}
# Forward actions that raise spend; only these are gated by the budget caps.
# Stopping or capping resources lowers spend and must stay possible when over budget.
COST_INCREASING_ACTIONS = {"start_ec2"}
# Actions that never call the cloud (a recorded recommendation for a human).
NO_CLOUD_ACTIONS = {"recommend_review"}
# RDS is never mutated automatically; metadata-only actions are allowed.
RDS_ALLOWED_ACTIONS = {"apply_tags", "remove_tags"} | NO_CLOUD_ACTIONS
# Metadata-only actions: exempt from (and not counted by) the daily cap and
# cooldown, which exist to bound state changes. Otherwise auto-tagging a large
# untagged estate would consume the whole cap and starve idle-EC2 stops. They
# have their own, larger daily cap (MAX_TAG_ACTIONS_PER_DAY) instead.
METADATA_ONLY_ACTIONS = {"apply_tags", "remove_tags"}
IN_FLIGHT_STATUSES = ["pending", "pending_approval", "approved", "executing"]


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


class SafetyLayer:
    def __init__(self):
        self.db = get_supabase_client()

    def check_proposal(
        self, action_type: str, resource_id: str, exclude_action_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """Static checks: may this action ever be proposed for this resource?"""
        if action_type in DESTRUCTIVE_ACTIONS:
            return False, f"Destructive action {action_type} is never allowed."

        res = self.db.table("resources").select("protected, tags, resource_type").eq("id", resource_id).execute()
        if not res.data:
            return False, "Resource not found in database."
        resource = res.data[0]

        if resource.get("protected") is True:
            return False, "Resource is explicitly marked as protected."
        tags = resource.get("tags") or {}
        if _truthy(tags.get("cloudsentry:protected")):
            return False, "Resource has 'cloudsentry:protected=true' tag."
        if _truthy(tags.get("cloudsentry:exempt")):
            return False, "Resource has cloudsentry:exempt=true tag."
        if action_type == "stop_ec2" and _truthy(tags.get("do-not-stop")):
            return False, "Resource has do-not-stop tag."
        if resource.get("resource_type") == "rds" and action_type not in RDS_ALLOWED_ACTIONS:
            return False, "RDS state changes are never automated."

        query = (
            self.db.table("optimization_actions")
            .select("id")
            .eq("resource_id", resource_id)
            .eq("action_type", action_type)
            .in_("status", IN_FLIGHT_STATUSES)
        )
        if exclude_action_id:
            query = query.neq("id", exclude_action_id)
        if query.execute().data:
            return False, "An equivalent action is already pending or executing for this resource."

        return True, "Passed proposal checks."

    def check_execution(
        self, action_type: str, resource_id: str, action_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """Full gate evaluated immediately before any mutating cloud call."""
        if action_type in NO_CLOUD_ACTIONS:
            # Acknowledging a recommendation changes nothing in the cloud, so
            # it stays possible while automation is stopped.
            return True, "No cloud change."
        if not runtime_config.automation_enabled():
            return False, "GLOBAL_AUTOMATION_ENABLED is False (Kill-switch activated)."

        ok, reason = self.check_proposal(action_type, resource_id, exclude_action_id=action_id)
        if not ok:
            return ok, reason

        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

        if action_type in METADATA_ONLY_ACTIONS:
            tag_cap = int(settings.MAX_TAG_ACTIONS_PER_DAY)
            tagged = self._metadata_executions_since(start_of_day, exclude_action_id=action_id)
            if len(tagged) >= tag_cap:
                return False, f"Daily tagging limit reached ({len(tagged)} >= {tag_cap})."
            return True, "Passed all safety checks."

        max_actions = int(runtime_config.get_flag("MAX_ACTIONS_PER_DAY", 5))
        today = self._real_executions_since(start_of_day, exclude_action_id=action_id)
        if len(today) >= max_actions:
            return False, f"Daily action limit reached ({len(today)} >= {max_actions})."

        cooldown_minutes = int(runtime_config.get_flag("ACTION_COOLDOWN_MINUTES", 30))
        window = now - timedelta(minutes=cooldown_minutes)
        recent = self._real_executions_since(window, resource_id=resource_id, exclude_action_id=action_id)
        if recent:
            return False, f"Resource is in cooldown period (last {cooldown_minutes} mins)."

        if action_type in COST_INCREASING_ACTIONS:
            over, why = self.budget_status(now)
            if over:
                return False, why

        return True, "Passed all safety checks."

    def check_rollback(self, action_type: str, resource_id: str) -> tuple[bool, str]:
        """Gate for operator-initiated rollbacks.

        Deliberately *not* gated by the kill-switch, cooldown or daily cap: an
        undo must stay possible while automation is stopped. Still enforced:
        destructive-action ban, protection tags, the RDS rule, no duplicate
        in-flight action, and the budget for actions that raise spend."""
        ok, reason = self.check_proposal(action_type, resource_id)
        if not ok:
            return ok, reason
        if action_type in COST_INCREASING_ACTIONS:
            over, why = self.budget_status()
            if over:
                return False, why
        return True, "Passed rollback checks."

    # Backwards-compatible name used by earlier callers/tests.
    def check_action_safe(self, action_type: str, resource_id: str, action_id: Optional[str] = None) -> tuple[bool, str]:
        return self.check_execution(action_type, resource_id, action_id)

    def _real_executions_since(
        self, since: datetime, resource_id: Optional[str] = None, exclude_action_id: Optional[str] = None
    ) -> list:
        """State-changing executions since `since` that count against the caps:

        * real (non-dry-run) actions that reached the cloud — executing,
          completed, rolled back, or failed *after* the mutating call
          (pre_state is only written right before it);
        * actions claimed but not yet submitted (executed_at still NULL).
          Their dry-run flag is not final yet, so they are counted
          conservatively; otherwise concurrent executions could each pass
          the cap check before any of them records executed_at.
        """
        def base():
            q = (
                self.db.table("optimization_actions")
                .select("id")
                .not_.in_("action_type", sorted(METADATA_ONLY_ACTIONS))
            )
            if resource_id:
                q = q.eq("resource_id", resource_id)
            if exclude_action_id:
                q = q.neq("id", exclude_action_id)
            return q

        submitted = (
            base().eq("dry_run", False)
            .in_("status", ["executing", "completed", "rolled_back"])
            .gte("executed_at", since.isoformat())
            .execute().data or []
        )
        failed_after_submit = (
            base().eq("dry_run", False).eq("status", "failed")
            .not_.is_("pre_state", "null")
            .gte("executed_at", since.isoformat())
            .execute().data or []
        )
        claimed = (
            base().eq("status", "executing")
            .is_("executed_at", "null")
            .gte("claimed_at", since.isoformat())
            .execute().data or []
        )
        seen: dict = {}
        for row in submitted + failed_after_submit + claimed:
            seen[row["id"]] = row
        return list(seen.values())

    def _metadata_executions_since(self, since: datetime, exclude_action_id: Optional[str] = None) -> list:
        """Real tag writes since `since`, plus claimed-not-yet-finished ones."""
        def base():
            q = self.db.table("optimization_actions").select("id").in_("action_type", sorted(METADATA_ONLY_ACTIONS))
            if exclude_action_id:
                q = q.neq("id", exclude_action_id)
            return q

        done = (base().eq("dry_run", False).in_("status", ["completed", "rolled_back"])
                .gte("executed_at", since.isoformat()).execute().data or [])
        claimed = (base().eq("status", "executing").gte("claimed_at", since.isoformat()).execute().data or [])
        return list({r["id"]: r for r in done + claimed}.values())

    def budget_status(self, now: Optional[datetime] = None) -> tuple[bool, str]:
        """Returns (over_budget, reason). Fails closed: unknown spend counts as over."""
        now = now or datetime.now(timezone.utc)
        daily_cap = float(runtime_config.get_flag("MAX_DAILY_SPEND_USD", 2.0))
        monthly_cap = float(runtime_config.get_flag("MAX_MONTHLY_BUDGET_USD", 10.0))
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_month = start_of_day.replace(day=1)
        try:
            rows = fetch_all(
                lambda: self.db.table("cost_records")
                .select("estimated_cost_usd, recorded_at")
                .gte("recorded_at", start_of_month.isoformat())
                .order("recorded_at")
            )
        except Exception as e:
            logger.error("Budget check failed; treating spend as unknown: %s", e)
            return True, "Spend could not be determined; budget-gated action blocked."

        monthly = 0.0
        daily = 0.0
        for r in rows:
            cost = float(r.get("estimated_cost_usd") or 0)
            monthly += cost
            recorded = _parse_ts(r.get("recorded_at"))
            if recorded and recorded >= start_of_day:
                daily += cost
        if daily >= daily_cap:
            return True, f"Daily spend cap reached ({daily:.2f} >= {daily_cap:.2f})."
        if monthly >= monthly_cap:
            return True, f"Monthly budget reached ({monthly:.2f} >= {monthly_cap:.2f})."
        return False, ""
