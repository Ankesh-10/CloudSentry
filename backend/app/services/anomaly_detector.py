import asyncio
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from backend.app.config import settings
from backend.app.db.asyncpg_pool import get_pool
from backend.app.db.pagination import fetch_all
from backend.app.db.supabase_client import get_supabase_client
from ml.baseline import check_idle_compute, check_runaway_lambda, detect_anomaly_ewma, detect_anomaly_zscore
from ml.features import build_feature_vector
from ml.inference import InferenceEngine

logger = logging.getLogger(__name__)

INACTIVE_STATES = ["terminated", "deleted"]
# Metric-based rules only run on data at least this fresh; otherwise the last
# samples before a stop would keep re-raising "idle" for a stopped instance.
MAX_SAMPLE_AGE = timedelta(minutes=30)
PRIMARY_METRIC = {"ec2": "CPUUtilization", "lambda": "Invocations"}
# Metric-behaviour anomalies (as opposed to state/metadata rules). They clear
# when the behaviour stops; see _resolve_quiet.
STATISTICAL_TYPES = ["ml_behavioral_anomaly", "statistical_anomaly", "unusual_cpu_spike"]
BEHAVIORAL_TYPES = ["runaway_lambda"] + STATISTICAL_TYPES
# An active behavioural anomaly is re-stamped (detected_at) every cycle it is
# still seen. Resolve only after it has gone unseen this long *and* the current
# cycle evaluated as normal, so one quiet sample does not flap it.
BEHAVIORAL_CLEAR_WINDOW = timedelta(minutes=30)
_IN_CHUNK = 200


def _num(value, default=0.0):
    """JSON/Postgres cannot store NaN or inf; numpy scalars are not JSON-serialisable."""
    if value is None:
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return f if np.isfinite(f) else default


class AnomalyDetectorService:
    def __init__(self):
        self.db = get_supabase_client()
        self.inference_engine = InferenceEngine()

    async def run(self):
        logger.info("Starting Anomaly Detection cycle...")
        pool = get_pool()
        try:
            await asyncio.to_thread(self.resolve_inactive_resources)
        except Exception:
            logger.exception("Could not resolve anomalies of terminated/deleted resources")
        resources = await asyncio.to_thread(
            fetch_all,
            lambda: self.db.table("resources")
            .select("id, resource_type, tags, state, metadata")
            .not_.in_("state", INACTIVE_STATES)
            .order("id"),
        )
        now = datetime.now(timezone.utc)
        failures = 0
        for r in resources:
            try:
                records = await self._fetch_metrics(pool, r["id"])
                await asyncio.to_thread(self.process_resource, r, records, now)
            except Exception:
                failures += 1
                logger.exception("Anomaly detection failed for resource %s", r["id"])
        logger.info("Anomaly Detection cycle completed (%s resources, %s failures).", len(resources), failures)
        if resources and failures == len(resources):
            raise RuntimeError("Anomaly detection failed for every resource")

    async def _fetch_metrics(self, pool, resource_id: str):
        async with pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT time, metric_name, value
                FROM resource_metrics
                WHERE resource_id = $1 AND time > NOW() - INTERVAL '30 days'
                ORDER BY time ASC
                """,
                resource_id,
            )

    # Synchronous: pandas + supabase-py. Runs in a worker thread.
    def process_resource(self, resource: dict, records, now: datetime) -> None:
        resource_id = resource["id"]
        resource_type = resource["resource_type"]
        detected_at = now.isoformat()

        # Rule-based, metadata-only checks. Each is independent: an untagged
        # instance must still be evaluated for idleness.
        self._apply_rule(resource_id, "untagged_resource", self._untagged_result(resource), detected_at)
        if resource_type == "ebs":
            self._apply_rule(resource_id, "unused_volume", self._unused_volume(resource), detected_at)

        if resource_type == "ec2" and resource.get("state") != "running":
            # Idle and behaviour only have meaning for a running instance.
            self._resolve_cleared(resource_id, "idle_compute", detected_at)
            self._resolve_types(resource_id, BEHAVIORAL_TYPES, detected_at)
            return
        if not records:
            return

        df = pd.DataFrame([dict(rec) for rec in records])
        df["time"] = pd.to_datetime(df["time"], utc=True)
        latest = df["time"].max()
        if now - latest.to_pydatetime() > MAX_SAMPLE_AGE:
            logger.debug("Skipping metric rules for %s: newest sample %s is stale", resource_id, latest)
            return

        idle_result = check_idle_compute(
            df,
            resource_type,
            cpu_threshold=settings.ML_IDLE_CPU_THRESHOLD_PCT,
            window_hours=settings.ML_IDLE_WINDOW_HOURS,
        )
        if resource_type == "ec2":
            self._apply_rule(resource_id, "idle_compute", idle_result, detected_at,
                             evaluable=idle_result.get("evaluable", True))
            if idle_result["is_anomaly"]:
                return

        runaway = check_runaway_lambda(df, resource_type)
        if runaway["is_anomaly"]:
            self._record_anomaly(resource_id, runaway, detected_at, runaway.get("features") or {})
            return
        if resource_type == "lambda":
            self._resolve_quiet(resource_id, ["runaway_lambda"], now)

        result = self._statistical(df, resource_type)
        if result.get("is_anomaly"):
            self._record_anomaly(resource_id, result, detected_at, result.pop("_features", {}))
            flagged = result.get("anomaly_type")
            self._resolve_quiet(resource_id, [t for t in STATISTICAL_TYPES if t != flagged], now)
        else:
            self._resolve_quiet(resource_id, STATISTICAL_TYPES, now)

    def _statistical(self, df: pd.DataFrame, resource_type: str) -> dict:
        """Cold-start gate: Z-score -> EWMA -> Isolation Forest by sample count."""
        metric = PRIMARY_METRIC.get(resource_type)
        point_count = df["time"].nunique()
        features_df = build_feature_vector(df, resource_type)
        features = features_df.iloc[-1].to_dict() if not features_df.empty else {}

        if point_count >= settings.ML_MIN_POINTS_IF:
            result = self.inference_engine.predict(features_df, resource_type)
            if result.get("reason") != "Model not trained":
                if result.get("is_anomaly"):
                    result.setdefault("anomaly_type", "ml_behavioral_anomaly")
                result["_features"] = features
                return result
        if metric is None:
            return {"is_anomaly": False}
        if point_count < settings.ML_MIN_POINTS_ZSCORE:
            result = detect_anomaly_zscore(df, metric, threshold=settings.ML_ZSCORE_THRESHOLD)
            result["model_version"] = "zscore_fallback"
        else:
            result = detect_anomaly_ewma(df, metric, threshold=settings.ML_ZSCORE_THRESHOLD)
            result["model_version"] = "ewma_fallback"
        result["_features"] = features
        return result

    def _untagged_result(self, resource: dict) -> dict:
        meta = resource.get("metadata") or {}
        if meta.get("tags_unreadable"):
            # Unknown is not "untagged": neither raise nor resolve.
            return {"is_anomaly": False, "evaluable": False}
        tags = resource.get("tags") or {}
        missing = [t for t in settings.required_tag_list() if t not in tags]
        if not missing:
            return {"is_anomaly": False}
        return {
            "is_anomaly": True,
            "anomaly_type": "untagged_resource",
            "severity": "LOW",
            "score": 1.0,
            "confidence": 0.9,
            "reason": f"Missing required tags: {', '.join(sorted(missing))}",
            "model_version": "rule_untagged",
        }

    def _unused_volume(self, resource: dict) -> dict:
        meta = resource.get("metadata") or {}
        if resource.get("state") == "available" or meta.get("unattached"):
            return {
                "is_anomaly": True,
                "anomaly_type": "unused_volume",
                "severity": "MEDIUM",
                "score": 1.0,
                "confidence": 0.85,
                "reason": "EBS volume is unattached (available).",
                "model_version": "rule_unused_volume",
            }
        return {"is_anomaly": False}

    def _apply_rule(self, resource_id: str, anomaly_type: str, result: dict, detected_at: str, evaluable: bool = True) -> None:
        if result.get("is_anomaly"):
            self._record_anomaly(resource_id, result, detected_at, result.get("features") or {})
        elif evaluable and result.get("evaluable", True):
            self._resolve_cleared(resource_id, anomaly_type, detected_at)

    def _resolve_cleared(self, resource_id: str, anomaly_type: str, now_iso: str) -> None:
        res = (
            self.db.table("anomalies")
            .update({"status": "resolved", "resolved_at": now_iso})
            .eq("resource_id", resource_id)
            .eq("anomaly_type", anomaly_type)
            .eq("status", "active")
            .execute()
        )
        if res.data:
            logger.info("Resolved cleared %s anomaly for resource %s", anomaly_type, resource_id)

    def _resolve_types(self, resource_id: str, anomaly_types: list, now_iso: str) -> None:
        res = (
            self.db.table("anomalies")
            .update({"status": "resolved", "resolved_at": now_iso})
            .eq("resource_id", resource_id)
            .in_("anomaly_type", anomaly_types)
            .eq("status", "active")
            .execute()
        )
        if res.data:
            logger.info("Resolved %s anomalies for resource %s", len(res.data), resource_id)

    def _resolve_quiet(self, resource_id: str, anomaly_types: list, now: datetime) -> None:
        """Resolve behavioural anomalies not re-observed within the clear window."""
        if not anomaly_types:
            return
        res = (
            self.db.table("anomalies")
            .update({"status": "resolved", "resolved_at": now.isoformat()})
            .eq("resource_id", resource_id)
            .in_("anomaly_type", anomaly_types)
            .eq("status", "active")
            .lt("detected_at", (now - BEHAVIORAL_CLEAR_WINDOW).isoformat())
            .execute()
        )
        if res.data:
            logger.info("Resolved %s cleared behavioural anomalies for resource %s", len(res.data), resource_id)

    def resolve_inactive_resources(self) -> int:
        """Terminated/deleted resources are skipped by detection, so nothing
        would ever clear their anomalies; close them here."""
        gone = fetch_all(
            lambda: self.db.table("resources").select("id").in_("state", INACTIVE_STATES).order("id")
        )
        ids = [r["id"] for r in gone]
        now_iso = datetime.now(timezone.utc).isoformat()
        resolved = 0
        for i in range(0, len(ids), _IN_CHUNK):
            res = (
                self.db.table("anomalies")
                .update({"status": "resolved", "resolved_at": now_iso})
                .in_("resource_id", ids[i:i + _IN_CHUNK])
                .eq("status", "active")
                .execute()
            )
            resolved += len(res.data or [])
        if resolved:
            logger.info("Resolved %s anomalies of terminated/deleted resources", resolved)
        return resolved

    def _record_anomaly(self, resource_id: str, result: dict, detected_at: str, features: dict):
        anomaly_type = result.get("anomaly_type", "unknown")
        features = {k: _num(v, None) for k, v in (features or {}).items()}
        score = _num(result.get("score"))
        confidence = _num(result.get("confidence"))
        existing = (
            self.db.table("anomalies")
            .select("id")
            .eq("resource_id", resource_id)
            .eq("anomaly_type", anomaly_type)
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if existing.data:
            self.db.table("anomalies").update({
                "anomaly_score": score,
                "confidence": confidence,
                "reason": result.get("reason", ""),
                "features_snapshot": features,
                "detected_at": detected_at,
            }).eq("id", existing.data[0]["id"]).execute()
            return

        payload = {
            "resource_id": resource_id,
            "anomaly_type": anomaly_type,
            "severity": result.get("severity", "LOW"),
            "anomaly_score": score,
            "confidence": confidence,
            "detected_at": detected_at,
            "reason": result.get("reason", ""),
            "features_snapshot": features,
            "model_version": result.get("model_version", "unknown"),
            "status": "active",
        }
        try:
            self.db.table("anomalies").insert(payload).execute()
        except Exception as e:
            # uq_anomalies_one_active: a concurrent cycle recorded it first.
            logger.warning("Could not insert %s anomaly for %s: %s", anomaly_type, resource_id, e)
            return
        logger.info("Recorded anomaly for resource %s: %s", resource_id, anomaly_type)
