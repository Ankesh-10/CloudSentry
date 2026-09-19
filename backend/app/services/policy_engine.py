import logging
import json
from datetime import datetime, timezone
from backend.app.db.supabase_client import get_supabase_client
from backend.app.config import settings
from backend.app.services.safety_layer import SafetyLayer
from backend.app.services.audit_logger import AuditLogger

logger = logging.getLogger(__name__)

class PolicyEngine:
    def __init__(self):
        self.db = get_supabase_client()
        self.safety = SafetyLayer()
        self.audit = AuditLogger()

    def _evaluate_condition(self, condition: dict, context: dict) -> bool:
        """
        Recursively evaluates a JSON condition tree against the context.
        """
        if "AND" in condition:
            return all(self._evaluate_condition(c, context) for c in condition["AND"])
        if "OR" in condition:
            return any(self._evaluate_condition(c, context) for c in condition["OR"])
            
        field_path = condition.get("field", "")
        op = condition.get("op")
        target_val = condition.get("value")
        
        parts = field_path.split(".")
        current_val = context
        try:
            for part in parts:
                current_val = current_val[part]
        except (KeyError, TypeError):
            return False
            
        if op == "eq": return current_val == target_val
        if op == "neq": return current_val != target_val
        if op == "gt": return current_val > target_val
        if op == "gte": return current_val >= target_val
        if op == "lt": return current_val < target_val
        if op == "lte": return current_val <= target_val
        
        return False

    def evaluate_all(self):
        """
        Evaluates active anomalies against policies to propose Optimization Actions.
        """
        logger.info("Evaluating active anomalies against Policy Engine...")
        
        res = self.db.table("anomalies").select("*, resources(*)").eq("status", "active").execute()
        anomalies = res.data
        if not anomalies:
            return
            
        res = self.db.table("policies").select("*").eq("enabled", True).order("priority", desc=True).execute()
        policies = res.data
        if not policies:
            return
            
        now = datetime.now(timezone.utc).isoformat()
        actions_to_create = []
        
        for anomaly in anomalies:
            resource = anomaly["resources"]
            
            # Check if this anomaly already has a pending/approved action
            res = self.db.table("optimization_actions").select("id").eq("anomaly_id", anomaly["id"]).in_("status", ["pending", "approved", "executing"]).execute()
            if res.data:
                continue
                
            context = {
                "anomaly": {
                    "anomaly_type": anomaly["anomaly_type"],
                    "confidence": anomaly["confidence"],
                    "score": anomaly["anomaly_score"],
                    "idle_score": anomaly["features_snapshot"].get("rolling_avg_cpu_24h", 100) if anomaly["anomaly_type"] == "idle_compute" else 0.0
                },
                "resource": {
                    "resource_type": resource["resource_type"],
                    "state": resource["state"],
                    "protected": resource["protected"],
                    "age_minutes": 100 # Stub for MVP
                }
            }
            
            matched_policy = None
            for policy in policies:
                if policy["resource_type"] != resource["resource_type"] and policy["resource_type"] != "*":
                    continue
                if policy["anomaly_type"] != anomaly["anomaly_type"] and policy["anomaly_type"] != "*":
                    continue
                    
                conditions = policy.get("conditions", {})
                if type(conditions) == str:
                    try:
                        conditions = json.loads(conditions)
                    except:
                        conditions = {}
                        
                if not conditions or self._evaluate_condition(conditions, context):
                    matched_policy = policy
                    break
                    
            if matched_policy:
                # Run through Safety Layer before creating action
                is_safe, reason = self.safety.check_action_safe(matched_policy["action_type"], resource["id"])
                
                if not is_safe:
                    logger.warning(f"Action {matched_policy['action_type']} blocked by Safety Layer: {reason}")
                    self.audit.log_action(
                        event_type="action_blocked",
                        actor="SYSTEM",
                        resource_id=resource["id"],
                        message=f"Blocked {matched_policy['action_type']}: {reason}"
                    )
                    continue

                actions_to_create.append({
                    "anomaly_id": anomaly["id"],
                    "resource_id": resource["id"],
                    "action_type": matched_policy["action_type"],
                    "risk_level": matched_policy["risk_level"],
                    "requires_approval": matched_policy["requires_approval"],
                    "status": "pending_approval" if matched_policy["requires_approval"] else "pending",
                    "dry_run": settings.DRY_RUN_MODE,
                    "created_at": now
                })
                
        if actions_to_create:
            self.db.table("optimization_actions").insert(actions_to_create).execute()
            logger.info(f"Proposed {len(actions_to_create)} new optimization actions.")
