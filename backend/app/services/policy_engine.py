import json
import logging
from datetime import datetime, timedelta, timezone

from backend.app.db.pagination import fetch_all
from backend.app.db.supabase_client import get_supabase_client
from backend.app.services import runtime_config
from backend.app.services.audit_logger import AuditLogger
from backend.app.services.cost_estimator import CostEstimationService
from backend.app.services.safety_layer import SafetyLayer

logger = logging.getLogger(__name__)

HOURS_PER_MONTH = 730.0
# An anomaly gets at most one live action. Once an action for it reached any of
# these states, the anomaly is never re-proposed (a new anomaly row is needed).
TERMINAL_NO_REPROPOSE = {"pending", "pending_approval", "approved", "executing", "completed", "rolled_back", "rejected"}
SAVINGS_BY_ACTION = {"stop_ec2"}


def _parse_ts(value):
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class PolicyEngine:
    def __init__(self):
        self.db = get_supabase_client()
        self.safety = SafetyLayer()
        self.costs = CostEstimationService()
        self.audit = AuditLogger()

    def _evaluate_condition(self, condition: dict, context: dict) -> bool:
        if not isinstance(condition, dict) or not condition:
            return False
        if "AND" in condition:
            clauses = condition["AND"]
            return bool(clauses) and all(self._evaluate_condition(c, context) for c in clauses)
        if "OR" in condition:
            return any(self._evaluate_condition(c, context) for c in condition["OR"])

        field_path = condition.get("field", "")
        op = condition.get("op")
        target_val = condition.get("value")
        current_val = context
        try:
            for part in field_path.split("."):
                current_val = current_val[part]
        except (KeyError, TypeError):
            return False

        try:
            if op == "eq":
                return current_val == target_val
            if op == "neq":
                return current_val != target_val
            if op == "gt":
                return current_val > target_val
            if op == "gte":
                return current_val >= target_val
            if op == "lt":
                return current_val < target_val
            if op == "lte":
                return current_val <= target_val
            if op == "in":
                return current_val in (target_val or [])
        except TypeError:
            return False
        return False

    def _should_skip(self, anomaly_id: str, now: datetime) -> bool:
        res = (
            self.db.table("optimization_actions")
            .select("status, created_at")
            .eq("anomaly_id", anomaly_id)
            .order("created_at", desc=True)
            .execute()
        )
        rows = res.data or []
        if any(r["status"] in TERMINAL_NO_REPROPOSE for r in rows):
            return True
        # Only failed attempts remain: back off for one cooldown period.
        if rows:
            last = _parse_ts(rows[0].get("created_at"))
            cooldown = int(runtime_config.get_flag("ACTION_COOLDOWN_MINUTES", 30))
            if last and now - last < timedelta(minutes=cooldown):
                return True
        return False

    @staticmethod
    def _load_conditions(policy: dict):
        conditions = policy.get("conditions") or {}
        if isinstance(conditions, str):
            try:
                conditions = json.loads(conditions)
            except (TypeError, ValueError):
                logger.warning("Policy %s has invalid JSON conditions; skipping", policy.get("id"))
                return None
        if not conditions:
            logger.warning("Policy %s has empty conditions; skipping", policy.get("id"))
            return None
        return conditions

    def _build_context(self, anomaly: dict, resource: dict, now: datetime) -> dict:
        first_seen = _parse_ts(resource.get("first_seen"))
        age_minutes = (now - first_seen).total_seconds() / 60.0 if first_seen else 0.0

        snapshot = anomaly.get("features_snapshot") or {}
        idle_score = 0.0
        if anomaly.get("anomaly_type") == "idle_compute":
            if snapshot.get("idle_score") is not None:
                idle_score = float(snapshot["idle_score"])
            elif snapshot.get("rolling_avg_cpu_24h") is not None:
                idle_score = max(0.0, min(1.0, 1.0 - float(snapshot["rolling_avg_cpu_24h"]) / 100.0))

        return {
            "anomaly": {
                "anomaly_type": anomaly["anomaly_type"],
                "severity": anomaly.get("severity"),
                "confidence": anomaly.get("confidence") or 0,
                "score": anomaly.get("anomaly_score") or 0,
                "idle_score": idle_score,
            },
            "resource": {
                "resource_type": resource.get("resource_type"),
                "state": resource.get("state"),
                "protected": bool(resource.get("protected")),
                "age_minutes": age_minutes,
            },
            "system": {
                "automation_enabled": runtime_config.automation_enabled(),
                "dry_run": runtime_config.dry_run_mode(),
            },
        }

    def evaluate_all(self) -> int:
        logger.info("Evaluating active anomalies against Policy Engine...")
        runtime_config.refresh_from_db()
        anomalies = fetch_all(
            lambda: self.db.table("anomalies").select("*, resources(*)").eq("status", "active").order("detected_at")
        )
        if not anomalies:
            return 0

        policies = (
            self.db.table("policies").select("*").eq("enabled", True).order("priority", desc=False).execute().data
        )
        if not policies:
            return 0

        now = datetime.now(timezone.utc)
        created = 0
        for anomaly in anomalies:
            resource = anomaly.get("resources") or {}
            if not resource:
                continue
            if self._should_skip(anomaly["id"], now):
                continue

            context = self._build_context(anomaly, resource, now)
            matched_policy = None
            for policy in policies:
                if policy.get("resource_type") not in (resource.get("resource_type"), "*"):
                    continue
                if policy.get("anomaly_type") not in (anomaly["anomaly_type"], "*"):
                    continue
                conditions = self._load_conditions(policy)
                if conditions is not None and self._evaluate_condition(conditions, context):
                    matched_policy = policy
                    break
            if not matched_policy:
                continue

            action_type = matched_policy["action_type"]
            ok, reason = self.safety.check_proposal(action_type, resource["id"])
            if not ok:
                logger.info("Not proposing %s for %s: %s", action_type, resource["id"], reason)
                continue

            savings = None
            if action_type in SAVINGS_BY_ACTION:
                savings = round(self.costs.estimate_hourly(resource) * HOURS_PER_MONTH, 2)

            try:
                inserted = self.db.table("optimization_actions").insert({
                    "anomaly_id": anomaly["id"],
                    "resource_id": resource["id"],
                    "action_type": action_type,
                    "risk_level": matched_policy["risk_level"],
                    "requires_approval": bool(matched_policy["requires_approval"]),
                    "status": "pending_approval" if matched_policy["requires_approval"] else "pending",
                    "dry_run": runtime_config.dry_run_mode(),
                    "estimated_savings_usd": savings,
                    "created_at": now.isoformat(),
                }).execute()
            except Exception as e:
                # e.g. uq_actions_one_in_flight lost a race with another replica.
                logger.warning("Could not create %s for %s: %s", action_type, resource["id"], e)
                continue
            created += 1
            # One row per proposal (an anomaly is proposed for at most once
            # while an action for it is live), so this cannot flood the trail.
            self.audit.log_action(
                event_type="action_proposed",
                actor="SYSTEM",
                resource_id=resource["id"],
                action_id=(inserted.data or [{}])[0].get("id"),
                aws_api_call=action_type,
                request_params={"policy_id": matched_policy.get("id"), "policy": matched_policy.get("name"),
                                "anomaly_id": anomaly["id"],
                                "requires_approval": bool(matched_policy["requires_approval"])},
                response_status="pending_approval" if matched_policy["requires_approval"] else "pending",
                message=f"Policy '{matched_policy.get('name')}' proposed {action_type}",
            )

        if created:
            logger.info("Proposed %s new optimization actions.", created)
        return created
