import asyncio
import datetime
import logging
import threading
from typing import Any, Dict, List, Tuple

from backend.app.adapters.base import CloudProviderError
from backend.app.adapters.factory import get_cloud_adapter
from backend.app.db.asyncpg_pool import get_pool
from backend.app.db.pagination import fetch_all
from backend.app.db.supabase_client import get_supabase_client
from backend.app.services import runtime_config

logger = logging.getLogger(__name__)

METRIC_MAP = {
    "ec2": [
        ("AWS/EC2", "CPUUtilization", "InstanceId", "Average", "Percent"),
        ("AWS/EC2", "NetworkIn", "InstanceId", "Average", "Bytes"),
        ("AWS/EC2", "NetworkOut", "InstanceId", "Average", "Bytes"),
    ],
    "lambda": [
        ("AWS/Lambda", "Invocations", "FunctionName", "Sum", "Count"),
        ("AWS/Lambda", "Duration", "FunctionName", "Average", "Milliseconds"),
        ("AWS/Lambda", "Errors", "FunctionName", "Sum", "Count"),
    ],
    "s3": [
        ("AWS/S3", "BucketSizeBytes", "BucketName", "Average", "Bytes"),
        ("AWS/S3", "NumberOfObjects", "BucketName", "Average", "Count"),
    ],
    "rds": [
        ("AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", "Average", "Count"),
        ("AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", "Average", "Bytes"),
    ],
    "ebs": [
        ("AWS/EBS", "VolumeReadOps", "VolumeId", "Sum", "Count"),
        ("AWS/EBS", "VolumeWriteOps", "VolumeId", "Sum", "Count"),
    ],
}

# Approximate CloudWatch GetMetricData calls this process has made in the current hour.
_cw_calls_this_hour = {"hour": None, "count": 0}
_cw_lock = threading.Lock()


def _note_cw_calls(n: int) -> bool:
    with _cw_lock:
        return _note_cw_calls_locked(n)


def _note_cw_calls_locked(n: int) -> bool:
    now = datetime.datetime.now(datetime.timezone.utc)
    hour = now.replace(minute=0, second=0, microsecond=0)
    if _cw_calls_this_hour["hour"] != hour:
        _cw_calls_this_hour["hour"] = hour
        _cw_calls_this_hour["count"] = 0
    limit = int(runtime_config.get_flag("MAX_CW_API_CALLS_PER_HOUR", 200))
    if _cw_calls_this_hour["count"] + n > limit:
        logger.warning("CloudWatch hourly call cap reached (%s/%s)", _cw_calls_this_hour["count"], limit)
        return False
    _cw_calls_this_hour["count"] += n
    return True


# (period_seconds, lookback) per resource type. CloudWatch publishes EC2/RDS/EBS
# datapoints several minutes late and S3 storage metrics once a day, so a single
# 5-minute window (the old behaviour) usually returned nothing. Overlapping
# windows are safe: inserts are idempotent on (resource_id, time, metric_name).
WINDOWS = {
    "ec2": (300, datetime.timedelta(minutes=30)),
    "lambda": (300, datetime.timedelta(minutes=30)),
    "rds": (300, datetime.timedelta(minutes=30)),
    "ebs": (300, datetime.timedelta(minutes=30)),
    "s3": (86400, datetime.timedelta(days=3)),
}
INACTIVE_STATES = ["terminated", "deleted"]


def build_queries(resources: List[Dict[str, Any]]) -> Dict[tuple, Tuple[List[Dict[str, Any]], Dict[str, Tuple[str, str, str]]]]:
    """Group GetMetricData queries by (period, lookback) so each group can use
    its own time window. Returns {window: (queries, id -> (resource_id, metric, unit))}."""
    groups: Dict[tuple, Tuple[List[Dict[str, Any]], Dict[str, Tuple[str, str, str]]]] = {}
    idx = 0
    for r in resources:
        rtype = r["resource_type"]
        specs = METRIC_MAP.get(rtype, [])
        if not specs:
            continue
        period, lookback = WINDOWS[rtype]
        queries, mapping = groups.setdefault((period, lookback), ([], {}))
        for namespace, metric, dim_name, stat, unit in specs:
            dimensions = [{"Name": dim_name, "Value": r["provider_id"]}]
            if rtype == "s3":
                storage = "StandardStorage" if metric == "BucketSizeBytes" else "AllStorageTypes"
                dimensions.append({"Name": "StorageType", "Value": storage})
            qid = f"q_{idx}"
            idx += 1
            queries.append({
                "Id": qid,
                "MetricStat": {
                    "Metric": {"Namespace": namespace, "MetricName": metric, "Dimensions": dimensions},
                    "Period": period,
                    "Stat": stat,
                },
                "ReturnData": True,
            })
            mapping[qid] = (r["id"], metric, unit)
    return groups


class TelemetryService:
    def __init__(self):
        self.cloud_adapter = get_cloud_adapter()
        self.db = get_supabase_client()
        self._regional: dict = {}

    def _load_resources(self) -> List[Dict[str, Any]]:
        return fetch_all(
            lambda: self.db.table("resources")
            .select("id, provider_id, resource_type, region")
            .not_.in_("state", INACTIVE_STATES)
            .order("id")
        )

    def _collect(self) -> List[tuple]:
        """Blocking part (supabase + boto3); runs in a worker thread."""
        resources = self._load_resources()
        if not resources:
            logger.info("No active resources found.")
            return []

        end_time = datetime.datetime.now(datetime.timezone.utc)
        records: List[tuple] = []
        # CloudWatch is regional: query each region's resources through that
        # region's client (S3 bucket metrics live in the bucket's region).
        by_region: Dict[Any, List[Dict[str, Any]]] = {}
        for r in resources:
            by_region.setdefault(r.get("region"), []).append(r)
        for region, region_resources in by_region.items():
            adapter = self._adapter_for(region)
            for (period, lookback), (queries, mapping) in build_queries(region_resources).items():
                chunks = max(1, (len(queries) + 499) // 500)
                if not _note_cw_calls(chunks):
                    logger.warning("Skipping %s CloudWatch queries (period=%ss): hourly cap reached",
                                   len(queries), period)
                    continue
                try:
                    results = adapter.get_metric_data(queries, end_time - lookback, end_time)
                except CloudProviderError:
                    # Provider failure is not "zero usage": record nothing for this group.
                    logger.error("CloudWatch call failed for region=%s period=%ss group", region, period)
                    continue
                for result in results:
                    qid = result.get("Id")
                    if qid not in mapping:
                        continue
                    resource_id, metric_name, unit = mapping[qid]
                    for t, v in zip(result.get("Timestamps") or [], result.get("Values") or []):
                        records.append((t, resource_id, metric_name, float(v), unit))
        return records

    def _adapter_for(self, region):
        default = getattr(self.cloud_adapter, "region", None)
        if not region or default is None or region == default:
            return self.cloud_adapter
        if region not in self._regional:
            self._regional[region] = get_cloud_adapter(region)
        return self._regional[region]

    async def run(self):
        logger.info("Starting Telemetry Collection cycle...")
        records = await asyncio.to_thread(self._collect)
        if not records:
            logger.info("No metric data returned from CloudWatch.")
            return {"inserted": 0}

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO resource_metrics (time, resource_id, metric_name, value, unit)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (resource_id, time, metric_name) DO NOTHING
                """,
                records,
            )
        logger.info("Upserted %s metric samples.", len(records))
        return {"inserted": len(records)}
