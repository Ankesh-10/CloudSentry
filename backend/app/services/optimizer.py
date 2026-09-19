import logging
from datetime import datetime, timezone
from backend.app.db.supabase_client import get_supabase_client
from backend.app.config import settings
import json

logger = logging.getLogger(__name__)

class OptimizerService:
    def __init__(self):
        self.db = get_supabase_client()
        
    def _evaluate_condition(self, condition: dict, context: dict) -> bool:
        """
        Recursively evaluates a JSON condition tree against the context.
        Example context: {"anomaly": {"idle_score": 0.9, "confidence": 0.8}, "resource": {"protected": False}}
        """
        if "AND" in condition:
            return all(self._evaluate_condition(c, context) for c in condition["AND"])
        if "OR" in condition:
            return any(self._evaluate_condition(c, context) for c in condition["OR"])
            
        field_path = condition.get("field", "")
        op = condition.get("op")
        target_val = condition.get("value")
        
        # Resolve field path
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

    def run(self):
        """
        Main entrypoint for APScheduler job.
        Evaluates active anomalies against policies to propose Optimization Actions.
        """
        logger.info("Starting Optimizer cycle...")
        
        # 1. Fetch active anomalies without pending actions
        # Using a subquery or left join would be better, but we can do it via API for MVP
        res = self.db.table("anomalies").select("*, resources(*)").eq("status", "active").execute()
        anomalies = res.data
        if not anomalies:
            return
            
        # 2. Fetch enabled policies
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
                
            # Build context for policy evaluation
            context = {
                "anomaly": {
                    "anomaly_type": anomaly["anomaly_type"],
                    "confidence": anomaly["confidence"],
                    "idle_score": anomaly["features_snapshot"].get("rolling_avg_cpu_24h", 100) if anomaly["anomaly_type"] == "idle_compute" else 0.0 # simplified
                },
                "resource": {
                    "resource_type": resource["resource_type"],
                    "state": resource["state"],
                    "protected": resource["protected"],
                    # Stub age to 100 minutes for MVP
                    "age_minutes": 100 
                }
            }
            
            # 3. Evaluate policies
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
                actions_to_create.append({
                    "anomaly_id": anomaly["id"],
                    "resource_id": resource["id"],
                    "action_type": matched_policy["action_type"],
                    "risk_level": matched_policy["risk_level"],
                    "requires_approval": matched_policy["requires_approval"],
                    "status": "pending",
                    "dry_run": settings.DRY_RUN_MODE,
                    "created_at": now
                })
                
        if actions_to_create:
            self.db.table("optimization_actions").insert(actions_to_create).execute()
            logger.info(f"Proposed {len(actions_to_create)} new optimization actions.")
