"""Hourly cost *estimates* from static list prices x observed state/usage.

Each run records the previous full clock hour, one row per resource, upserted
on (resource_id, recorded_at) so restarts and overlapping runs never double
count. These are estimates (source='estimated'), not billing data.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.app.config import settings
from backend.app.db.pagination import fetch_all
from backend.app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

HOURS_PER_MONTH = 730.0
BYTES_PER_GB = 1024 ** 3
BILLABLE_EC2_STATES = {"running", "pending", "stopping"}
BILLABLE_RDS_STATES = {"available", "backing-up", "modifying", "starting", "stopping"}
PRICING_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../data/pricing"))


def load_pricing(region: str) -> dict:
    path = os.path.join(PRICING_DIR, f"aws_{region.replace('-', '_')}.json")
    if not os.path.exists(path):
        logger.warning("Pricing file for %s not found. Falling back to us-east-1.", region)
        path = os.path.join(PRICING_DIR, "aws_us_east_1.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.error("Failed to load pricing JSON %s: %s", path, e)
        return {}


class CostEstimationService:
    def __init__(self):
        self.db = get_supabase_client()
        self.pricing = load_pricing(settings.AWS_DEFAULT_REGION)
        self._unpriced_warned: set[str] = set()

    def _warn_unpriced(self, key: str) -> None:
        if key not in self._unpriced_warned:
            self._unpriced_warned.add(key)
            logger.warning("No list price for %s; its cost is not estimated.", key)

    def estimate_hourly(self, resource: dict, usage: Optional[dict] = None) -> float:
        """Estimated USD for one hour of this resource in its current state."""
        rtype = resource.get("resource_type")
        meta = resource.get("metadata") or {}
        state = resource.get("state")
        usage = usage or {}

        if rtype == "ec2":
            if state not in BILLABLE_EC2_STATES:
                return 0.0
            inst_type = meta.get("instance_type")
            price = self.pricing.get("ec2", {}).get(inst_type)
            if price is None:
                self._warn_unpriced(f"ec2:{inst_type}")
                return 0.0
            return float(price)

        if rtype == "rds":
            if state not in BILLABLE_RDS_STATES:
                return 0.0
            cls = meta.get("instance_class")
            price = self.pricing.get("rds", {}).get(cls)
            if price is None:
                self._warn_unpriced(f"rds:{cls}")
                return 0.0
            return float(price)

        if rtype == "ebs":
            # Volumes bill for provisioned size whether attached or not.
            vtype = meta.get("volume_type") or "gp3"
            per_gb = self.pricing.get("ebs", {}).get(f"{vtype}_per_gb_month")
            if per_gb is None:
                self._warn_unpriced(f"ebs:{vtype}")
                return 0.0
            return float(per_gb) * float(meta.get("size") or 0) / HOURS_PER_MONTH

        if rtype == "s3":
            size_bytes = usage.get("BucketSizeBytes")
            if not size_bytes:
                return 0.0
            per_gb = float(self.pricing.get("s3", {}).get("standard_per_gb_month", 0.0))
            return per_gb * (float(size_bytes) / BYTES_PER_GB) / HOURS_PER_MONTH

        if rtype == "lambda":
            invocations = float(usage.get("Invocations") or 0.0)
            avg_ms = float(usage.get("Duration") or 0.0)
            if invocations <= 0:
                return 0.0
            lam = self.pricing.get("lambda", {})
            memory_mb = float(meta.get("memory") or 128)
            per_ms = float(lam.get("per_1ms_128mb", 0.0)) * (memory_mb / 128.0)
            compute = invocations * avg_ms * per_ms
            requests = invocations / 1_000_000 * float(lam.get("per_1M_requests", 0.0))
            return compute + requests

        return 0.0

    def _usage(self, resource: dict, start: datetime, end: datetime) -> dict:
        """Usage inputs from collected CloudWatch samples (S3 size, Lambda calls)."""
        rtype = resource.get("resource_type")
        if rtype == "s3":
            res = (
                self.db.table("resource_metrics")
                .select("value")
                .eq("resource_id", resource["id"])
                .eq("metric_name", "BucketSizeBytes")
                .order("time", desc=True)
                .limit(1)
                .execute()
            )
            return {"BucketSizeBytes": res.data[0]["value"]} if res.data else {}
        if rtype == "lambda":
            rows = (
                self.db.table("resource_metrics")
                .select("metric_name, value")
                .eq("resource_id", resource["id"])
                .in_("metric_name", ["Invocations", "Duration"])
                .gte("time", start.isoformat())
                .lt("time", end.isoformat())
                .execute()
            ).data or []
            invocations = sum(r["value"] for r in rows if r["metric_name"] == "Invocations")
            durations = [r["value"] for r in rows if r["metric_name"] == "Duration"]
            avg = sum(durations) / len(durations) if durations else 0.0
            return {"Invocations": invocations, "Duration": avg}
        return {}

    def run(self) -> int:
        logger.info("Starting Cost Estimation cycle...")
        if not self.pricing:
            logger.error("No pricing data loaded; skipping cost estimation.")
            return 0
        resources = fetch_all(
            lambda: self.db.table("resources")
            .select("id, resource_type, metadata, state")
            .not_.in_("state", ["terminated", "deleted"])
            .order("id")
        )
        hour_end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        hour_start = hour_end - timedelta(hours=1)

        records = []
        for r in resources:
            usage = self._usage(r, hour_start, hour_end) if r.get("resource_type") in ("s3", "lambda") else {}
            cost = self.estimate_hourly(r, usage)
            if cost <= 0:
                continue
            records.append({
                "resource_id": r["id"],
                "estimated_cost_usd": round(cost, 8),
                "source": "estimated",
                "billing_period_start": hour_start.date().isoformat(),
                "billing_period_end": hour_end.date().isoformat(),
                "recorded_at": hour_start.isoformat(),
            })
        for i in range(0, len(records), 500):
            self.db.table("cost_records").upsert(records[i:i + 500], on_conflict="resource_id,recorded_at").execute()
        logger.info("Recorded %s hourly cost estimates for %s.", len(records), hour_start.isoformat())
        return len(records)
