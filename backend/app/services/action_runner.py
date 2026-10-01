import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.app.adapters.factory import get_cloud_adapter
from backend.app.config import settings
from backend.app.db.supabase_client import get_supabase_client
from backend.app.services import runtime_config
from backend.app.services.audit_logger import AuditLogger
from backend.app.services.safety_layer import DESTRUCTIVE_ACTIONS, SafetyLayer

logger = logging.getLogger(__name__)

REVERSIBLE = {
    "stop_ec2": "start_ec2",
    "limit_lambda": "restore_lambda_concurrency",
}

VERIFY_TARGETS = {
    "stop_ec2": "stopped",
    "start_ec2": "running",
}

VERIFY_TIMEOUT_SECONDS = 300
# A claim that never reached the submit step (process crash/restart) is failed
# after this long so the resource is not blocked forever.
STALE_CLAIM_SECONDS = 900
AUTO_TAG_VALUE = "unassigned"
AUTO_TAG_MARKER = "cloudsentry:auto-tagged"
# Live state a resource must be in for the action to make sense; checked
# against the cloud right before the mutating call (the DB copy may be stale).
EXPECTED_LIVE_STATE = {
    "stop_ec2": "running",
    "start_ec2": "stopped",
}
EXPIRED_MESSAGE = "Expired: not executed within APPROVAL_TTL_HOURS of being proposed/approved"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


class ActionRunner:
    def __init__(self):
        self.db = get_supabase_client()
        self.cloud = get_cloud_adapter()
        self.audit = AuditLogger()
        self.safety = SafetyLayer()

    # ------------------------------------------------------------------ execute

    # ------------------------------------------------------------------- expiry

    def _ttl(self) -> timedelta:
        return timedelta(hours=settings.APPROVAL_TTL_HOURS)

    def is_expired(self, action: dict) -> bool:
        """A proposal (or approval) older than the TTL describes a world that
        may no longer exist; it must be re-proposed, not executed."""
        status = action.get("status")
        if status in ("pending", "pending_approval"):
            ref = _parse_ts(action.get("created_at"))
        elif status == "approved":
            ref = _parse_ts(action.get("approved_at")) or _parse_ts(action.get("created_at"))
        else:
            return False
        return ref is not None and _now() - ref > self._ttl()

    def expire(self, action: dict, actor: str = "SYSTEM") -> bool:
        res = (
            self.db.table("optimization_actions")
            .update({"status": "rejected", "post_state": {"result": EXPIRED_MESSAGE}})
            .eq("id", action["id"])
            .eq("status", action["status"])
            .execute()
        )
        if res.data:
            self.audit.log_action(
                event_type="action_expired",
                actor=actor,
                resource_id=action.get("resource_id"),
                action_id=action["id"],
                aws_api_call=action.get("action_type"),
                response_status="rejected",
                message=EXPIRED_MESSAGE,
            )
        return bool(res.data)

    def expire_stale_proposals(self) -> int:
        cutoff = (_now() - self._ttl()).isoformat()
        res = (
            self.db.table("optimization_actions")
            .select("id, status, resource_id, action_type, created_at, approved_at")
            .in_("status", ["pending", "pending_approval", "approved"])
            .lt("created_at", cutoff)
            .execute()
        )
        return sum(1 for row in res.data or [] if self.is_expired(row) and self.expire(row))

    def execute_pending_auto(self) -> int:
        """Execute pending actions that do not require approval."""
        self.expire_stale_proposals()
        runtime_config.refresh_from_db()
        if not runtime_config.automation_enabled():
            return 0
        res = (
            self.db.table("optimization_actions")
            .select("id")
            .eq("status", "pending")
            .eq("requires_approval", False)
            .order("created_at")
            .limit(50)
            .execute()
        )
        return sum(1 for row in (res.data or []) if self.execute_action(row["id"]))

    def _claim(self, action: dict) -> bool:
        """Compare-and-swap into `executing` so concurrent callers (scheduler,
        API, a second replica) can never execute the same action twice."""
        status = action["status"]
        if status == "approved":
            pass
        elif status == "pending" and not action.get("requires_approval"):
            pass
        else:
            return False
        res = (
            self.db.table("optimization_actions")
            .update({"status": "executing", "claimed_at": _now().isoformat()})
            .eq("id", action["id"])
            .eq("status", status)
            .execute()
        )
        return bool(res.data)

    def execute_action(self, action_id: str, actor: str = "SYSTEM") -> bool:
        logger.info("Executing action %s...", action_id)
        res = self.db.table("optimization_actions").select("*, resources(*)").eq("id", action_id).execute()
        if not res.data:
            logger.error("Action %s not found.", action_id)
            return False

        action = res.data[0]
        resource = action.get("resources") or {}
        if not resource:
            logger.error("Action %s has no resource.", action_id)
            return False

        if self.is_expired(action):
            self.expire(action, actor)
            logger.warning("Action %s expired before execution", action_id)
            return False

        if not self._claim(action):
            logger.warning("Action %s not claimable from status %s", action_id, action["status"])
            return False

        if action["action_type"] in DESTRUCTIVE_ACTIONS:
            self._finish(action_id, resource, action, False, "Destructive action blocked architecturally.", actor=actor)
            return False

        audit_ok = self.audit.log_action(
            event_type="action_started",
            actor=actor,
            resource_id=resource["id"],
            action_id=action_id,
            aws_api_call=action["action_type"],
            request_params={"provider_id": resource["provider_id"]},
            message=f"Starting execution of {action['action_type']} on {resource['provider_id']}",
        )
        if not audit_ok:
            self._finish(action_id, resource, action, False, "Aborted: failed to write pre-action audit log.", actor=actor)
            return False

        # Re-read the kill-switch from the database so a flag flipped by another
        # process (or directly in Supabase) is honoured before touching AWS.
        if not runtime_config.refresh_from_db():
            self._finish(action_id, resource, action, False, "Blocked: could not confirm kill-switch state from database.", actor=actor, blocked=True)
            return False
        is_safe, reason = self.safety.check_execution(action["action_type"], resource["id"], action_id=action_id)
        if not is_safe:
            self._finish(action_id, resource, action, False, f"Blocked by safety layer: {reason}", actor=actor, blocked=True)
            return False

        # The proposal was made for a specific anomaly; if that has since been
        # resolved or dismissed (e.g. the idle instance got busy), stand down.
        stale = self._stale_anomaly_reason(action)
        if stale:
            self._finish(action_id, resource, action, False, f"Blocked: {stale}", actor=actor, blocked=True)
            return False

        now = _now().isoformat()
        # The mode in force *now* governs; the row's dry_run is rewritten to the
        # mode actually used so the audit trail is truthful.
        if runtime_config.dry_run_mode():
            message = f"Dry run mode: WOULD {action['action_type']} on {resource['provider_id']}"
            self._finish(action_id, resource, action, True, message, executed_at=now, dry=True, actor=actor)
            return True

        live_problem = self._live_state_problem(action, resource)
        if live_problem:
            self._finish(action_id, resource, action, False, f"Blocked: {live_problem}", actor=actor, blocked=True)
            return False

        try:
            pre_state = self._capture_pre_state(action, resource)
            self.db.table("optimization_actions").update({
                "pre_state": pre_state,
                "dry_run": False,
            }).eq("id", action_id).execute()
            success, message, needs_verify = self._mutate(action, resource)
        except Exception:
            logger.exception("Execution error for %s", action_id)
            success, message, needs_verify = False, "Execution error", False

        if success and needs_verify:
            self.db.table("optimization_actions").update({
                "status": "executing",
                "executed_at": now,
                "post_state": {"result": message, "pending_verification": True},
            }).eq("id", action_id).execute()
            self.audit.log_action(
                event_type="action_submitted",
                actor=actor,
                resource_id=resource["id"],
                action_id=action_id,
                aws_api_call=action["action_type"],
                response_status="executing",
                message=message,
            )
            return True

        self._finish(action_id, resource, action, success, message, executed_at=now, actor=actor)
        if success:
            self._resolve_anomaly(action)
        return success

    # ------------------------------------------------------------------- verify

    def fail_stale_claims(self) -> int:
        cutoff = (_now().timestamp() - STALE_CLAIM_SECONDS)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        res = (
            self.db.table("optimization_actions")
            .update({
                "status": "failed",
                "post_state": {"result": "Interrupted before submission (stale claim)", "verified": False},
            })
            .eq("status", "executing")
            .is_("executed_at", "null")
            .lt("claimed_at", cutoff_iso)
            .execute()
        )
        for row in res.data or []:
            self._release_rollback_link(row["id"])
            self.audit.log_action(
                event_type="action_failed",
                actor="SYSTEM",
                resource_id=row.get("resource_id"),
                action_id=row.get("id"),
                aws_api_call=row.get("action_type"),
                response_status="failed",
                message="Execution interrupted before submission; state unknown, verify manually.",
            )
        return len(res.data or [])

    def verify_pending(self) -> int:
        self.fail_stale_claims()
        res = (
            self.db.table("optimization_actions")
            .select("*, resources(*)")
            .eq("status", "executing")
            .not_.is_("executed_at", "null")
            .execute()
        )
        verified = 0
        now = _now()
        for action in res.data or []:
            resource = action.get("resources") or {}
            action_type = action["action_type"]
            started = _parse_ts(action.get("executed_at"))
            timed_out = started is not None and (now - started).total_seconds() > VERIFY_TIMEOUT_SECONDS

            ok = False
            observed = None
            provider_id = resource.get("provider_id")
            if not provider_id:
                timed_out = True
            elif action_type in VERIFY_TARGETS:
                observed = self.cloud.get_instance_state(provider_id)
                ok = observed == VERIFY_TARGETS[action_type]
            elif action_type == "limit_lambda":
                observed = self.cloud.get_function_concurrency(provider_id)
                ok = observed == settings.LAMBDA_CONCURRENCY_LIMIT
            else:
                # No verifier for this type: never report success we cannot observe.
                timed_out = True

            if ok:
                self.db.table("optimization_actions").update({
                    "status": "completed",
                    "verified_at": now.isoformat(),
                    "post_state": {"state": observed, "verified": True},
                }).eq("id", action["id"]).eq("status", "executing").execute()
                if action_type in VERIFY_TARGETS and resource.get("id"):
                    self.db.table("resources").update({"state": observed}).eq("id", resource["id"]).execute()
                self.audit.log_action(
                    event_type="action_verified",
                    actor="SYSTEM",
                    resource_id=resource.get("id"),
                    action_id=action["id"],
                    aws_api_call=action_type,
                    response_status="completed",
                    message=f"Verified target state: {observed}",
                )
                self._after_success(action)
                verified += 1
            elif timed_out:
                self.db.table("optimization_actions").update({
                    "status": "failed",
                    "post_state": {"state": observed, "verified": False, "timeout": True},
                }).eq("id", action["id"]).eq("status", "executing").execute()
                self._release_rollback_link(action["id"])
                self.audit.log_action(
                    event_type="action_failed",
                    actor="SYSTEM",
                    resource_id=resource.get("id"),
                    action_id=action["id"],
                    aws_api_call=action_type,
                    response_status="failed",
                    message=f"Verification did not reach target state within {VERIFY_TIMEOUT_SECONDS}s (observed={observed})",
                )
        return verified

    def _after_success(self, action: dict) -> None:
        if action["action_type"] in REVERSIBLE or action["action_type"] == "apply_tags":
            self._resolve_anomaly(action)
            return
        # A completed rollback marks the original forward action as rolled back.
        original = (
            self.db.table("optimization_actions")
            .select("id")
            .eq("rollback_action_id", action["id"])
            .execute()
        )
        for row in original.data or []:
            self.db.table("optimization_actions").update({"status": "rolled_back"}).eq("id", row["id"]).execute()

    def _resolve_anomaly(self, action: dict) -> None:
        anomaly_id = action.get("anomaly_id")
        if not anomaly_id:
            return
        self.db.table("anomalies").update({
            "status": "resolved",
            "resolved_at": _now().isoformat(),
        }).eq("id", anomaly_id).eq("status", "active").execute()

    # ----------------------------------------------------------------- rollback

    def rollback_action(self, action_id: str, user_id: str = "SYSTEM") -> bool:
        """Reverse a completed action. Operator-initiated, so it is allowed while
        the kill-switch is on, but it is always dry-run if the original was."""
        logger.info("Rolling back action %s...", action_id)
        res = self.db.table("optimization_actions").select("*, resources(*)").eq("id", action_id).execute()
        if not res.data:
            return False

        original = res.data[0]
        resource = original.get("resources") or {}
        if original["status"] != "completed" or original.get("rollback_action_id"):
            logger.error("Action %s is not in a rollback-able state.", action_id)
            return False

        reverse_type = REVERSIBLE.get(original["action_type"])
        if not reverse_type or not resource:
            logger.error("Action %s is not reversible.", original["action_type"])
            return False

        ok, reason = self.safety.check_rollback(reverse_type, resource["id"])
        if not ok:
            logger.warning("Rollback of %s blocked: %s", action_id, reason)
            self.audit.log_action(
                event_type="rollback_blocked",
                actor=user_id,
                resource_id=resource["id"],
                action_id=action_id,
                aws_api_call=reverse_type,
                response_status="blocked",
                message=f"Rollback blocked by safety layer: {reason}",
            )
            return False

        original_was_dry = bool(original.get("dry_run")) or bool((original.get("post_state") or {}).get("dry_run"))
        dry = original_was_dry or runtime_config.dry_run_mode()
        now = _now().isoformat()
        try:
            new_action_res = self.db.table("optimization_actions").insert({
                "anomaly_id": original.get("anomaly_id"),
                "resource_id": resource["id"],
                "action_type": reverse_type,
                "risk_level": "LOW",
                "status": "executing",
                "claimed_at": now,
                "requires_approval": False,
                "approved_by": user_id,
                "approved_at": now,
                "dry_run": dry,
                "created_at": now,
            }).execute()
        except Exception:
            # Most likely uq_actions_one_in_flight: another action of this type
            # is already in flight for the resource.
            logger.exception("Could not create rollback action for %s", action_id)
            self.audit.log_action(
                event_type="rollback_failed",
                actor=user_id,
                resource_id=resource["id"],
                action_id=action_id,
                aws_api_call=reverse_type,
                response_status="failed",
                message="Could not create rollback action (an equivalent action may already be in flight).",
            )
            return False
        rollback_action_id = new_action_res.data[0]["id"]

        link = (
            self.db.table("optimization_actions")
            .update({"rollback_action_id": rollback_action_id})
            .eq("id", action_id)
            .is_("rollback_action_id", "null")
            .execute()
        )
        if not link.data:
            self.db.table("optimization_actions").update({
                "status": "failed",
                "post_state": {"result": "Superseded by a concurrent rollback"},
            }).eq("id", rollback_action_id).execute()
            return False

        self.audit.log_action(
            event_type="rollback_started",
            actor=user_id,
            resource_id=resource["id"],
            action_id=rollback_action_id,
            aws_api_call=reverse_type,
            message=f"Starting rollback of action {action_id}",
        )

        needs_verify = False
        if dry:
            message = f"Dry run mode: WOULD {reverse_type} on {resource['provider_id']}"
            success = True
        else:
            try:
                if reverse_type == "start_ec2":
                    success = self.cloud.start_instance(resource["provider_id"])
                    message = "EC2 start requested." if success else "Failed to start EC2."
                    needs_verify = success
                else:
                    stored = (original.get("pre_state") or {}).get("reserved_concurrency")
                    if stored is None:
                        success = self.cloud.remove_function_concurrency(resource["provider_id"])
                    else:
                        success = self.cloud.limit_function_concurrency(resource["provider_id"], int(stored))
                    if success:
                        success = self.cloud.get_function_concurrency(resource["provider_id"]) == stored
                    message = "Lambda concurrency restored." if success else "Failed to restore concurrency."
            except Exception:
                logger.exception("Rollback error for %s", action_id)
                success, message = False, "Rollback execution error"

        if needs_verify:
            self.db.table("optimization_actions").update({
                "executed_at": now,
                "post_state": {"result": message, "pending_verification": True},
            }).eq("id", rollback_action_id).execute()
            return True

        final_status = "completed" if success else "failed"
        self.db.table("optimization_actions").update({
            "status": final_status,
            "executed_at": now,
            "verified_at": now if success else None,
            "post_state": {"result": message, "dry_run": dry},
        }).eq("id", rollback_action_id).execute()
        if success:
            self.db.table("optimization_actions").update({"status": "rolled_back"}).eq("id", action_id).execute()
        else:
            self._release_rollback_link(rollback_action_id)
        self.audit.log_action(
            event_type="rollback_completed" if success else "rollback_failed",
            actor=user_id,
            resource_id=resource["id"],
            action_id=rollback_action_id,
            aws_api_call=reverse_type,
            response_status=final_status,
            message=message,
        )
        return success

    # ------------------------------------------------------------------ helpers

    def _stale_anomaly_reason(self, action: dict) -> Optional[str]:
        anomaly_id = action.get("anomaly_id")
        if not anomaly_id:
            return None
        try:
            res = self.db.table("anomalies").select("status").eq("id", anomaly_id).execute()
        except Exception:
            logger.exception("Could not read anomaly %s", anomaly_id)
            return "could not confirm the triggering anomaly is still active"
        if not res.data:
            return "triggering anomaly no longer exists"
        status = res.data[0].get("status")
        if status != "active":
            return f"triggering anomaly is {status}, not active"
        return None

    def _live_state_problem(self, action: dict, resource: dict) -> Optional[str]:
        expected = EXPECTED_LIVE_STATE.get(action["action_type"])
        if not expected:
            return None
        try:
            observed = self.cloud.get_instance_state(resource["provider_id"])
        except Exception:
            logger.exception("Live state read failed for %s", resource.get("provider_id"))
            observed = None
        if observed is None:
            return "could not read the live resource state"
        if observed != expected:
            return f"live state is {observed}, expected {expected}"
        return None

    def _release_rollback_link(self, rollback_action_id: str) -> None:
        """A failed rollback must not block a retry: unlink it from the
        original (which is still `completed`)."""
        try:
            self.db.table("optimization_actions").update({"rollback_action_id": None}) \
                .eq("rollback_action_id", rollback_action_id).eq("status", "completed").execute()
        except Exception:
            logger.exception("Could not release rollback link for %s", rollback_action_id)

    def _capture_pre_state(self, action, resource) -> dict:
        pre = {"state": resource.get("state"), "metadata": resource.get("metadata"), "tags": resource.get("tags")}
        if action["action_type"] == "limit_lambda":
            pre["reserved_concurrency"] = self.cloud.get_function_concurrency(resource["provider_id"])
        if action["action_type"] == "stop_ec2":
            pre["aws_state"] = self.cloud.get_instance_state(resource["provider_id"])
        return pre

    def _missing_tags(self, resource: dict) -> dict:
        tags = resource.get("tags") or {}
        missing = {k: AUTO_TAG_VALUE for k in settings.required_tag_list() if k not in tags}
        if missing:
            missing[AUTO_TAG_MARKER] = "true"
        return missing

    def _mutate(self, action, resource) -> tuple[bool, str, bool]:
        action_type = action["action_type"]
        provider_id = resource["provider_id"]
        if action_type == "stop_ec2":
            success = self.cloud.stop_instance(provider_id)
            return success, "EC2 stop requested." if success else "Failed to stop EC2.", True
        if action_type == "start_ec2":
            success = self.cloud.start_instance(provider_id)
            return success, "EC2 start requested." if success else "Failed to start EC2.", True
        if action_type == "limit_lambda":
            limit = settings.LAMBDA_CONCURRENCY_LIMIT
            if limit <= 0:
                return False, "Refusing to set Lambda concurrency <= 0 (hard disable).", False
            success = self.cloud.limit_function_concurrency(provider_id, limit)
            return success, f"Lambda concurrency limited to {limit}." if success else "Failed to limit concurrency.", True
        if action_type == "apply_tags":
            new_tags = self._missing_tags(resource)
            if not new_tags:
                return True, "Required tags already present; nothing to apply.", False
            tag_target = provider_id
            if resource["resource_type"] in ("lambda", "rds"):
                tag_target = (resource.get("metadata") or {}).get("arn")
                if not tag_target:
                    return False, "Resource ARN unknown; cannot tag.", False
            success = self.cloud.apply_tags(tag_target, new_tags, resource["resource_type"])
            if success:
                merged = {**(resource.get("tags") or {}), **new_tags}
                self.db.table("resources").update({"tags": merged}).eq("id", resource["id"]).execute()
            return success, f"Applied tags {sorted(new_tags)}." if success else "Failed to apply tags.", False
        return False, f"Unsupported action type: {action_type}", False

    def _finish(self, action_id, resource, action, success, message, executed_at=None, dry=False, actor="SYSTEM", blocked=False):
        final_status = "completed" if success else "failed"
        executed_at = executed_at or _now().isoformat()
        payload = {
            "status": final_status,
            "executed_at": executed_at,
            "post_state": {"result": message, "dry_run": dry},
        }
        if dry:
            payload["dry_run"] = True
        if success:
            payload["verified_at"] = executed_at
        self.db.table("optimization_actions").update(payload).eq("id", action_id).execute()
        event = "action_completed" if success else ("action_blocked" if blocked else "action_failed")
        self.audit.log_action(
            event_type=event,
            actor=actor,
            resource_id=resource["id"],
            action_id=action_id,
            aws_api_call=action["action_type"],
            response_status=final_status,
            message=message,
        )
