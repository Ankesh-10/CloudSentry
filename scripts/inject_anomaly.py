"""Inject synthetic CPU samples (11 normal + 1 spike) for one running EC2
resource, so the Z-score detector raises an anomaly on its next cycle.

This writes fake data into resource_metrics with the service-role key. It
refuses to run unless you opt in explicitly, and is meant for demo/dev
databases only:

    CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS=1 python scripts/inject_anomaly.py
    CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS=1 python scripts/inject_anomaly.py --cleanup <resource_id>

--cleanup removes the samples this script wrote (the last hour of
CPUUtilization for that resource).
"""
import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from supabase import Client, create_client

WINDOW = timedelta(minutes=60)


def _client() -> Client:
    load_dotenv()
    if os.environ.get("CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS") != "1":
        sys.exit("Refusing to write synthetic metrics: set CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS=1 "
                 "(demo/dev databases only).")
    if os.environ.get("ENVIRONMENT", "").lower() in ("prod", "production"):
        sys.exit("Refusing to run with ENVIRONMENT=production.")
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        sys.exit("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
    return create_client(url, key)


def inject(db: Client) -> None:
    res = (
        db.table("resources").select("id, provider_id, tags, protected")
        .eq("resource_type", "ec2").eq("state", "running").eq("protected", False)
        .limit(20).execute()
    )
    candidates = [r for r in res.data or []
                  if str((r.get("tags") or {}).get("cloudsentry:protected", "")).lower() not in ("true", "1", "yes")]
    if not candidates:
        sys.exit("No running, unprotected EC2 resources found. Run discovery first.")
    resource = candidates[0]
    print(f"Injecting anomaly into resource {resource['id']} ({resource['provider_id']})...")

    now = datetime.now(timezone.utc)
    payload = []
    for i in range(12, 0, -1):
        value = random.uniform(90.0, 99.0) if i == 1 else random.uniform(15.0, 25.0)
        payload.append({
            "time": (now - timedelta(minutes=5 * i)).isoformat(),
            "resource_id": resource["id"],
            "metric_name": "CPUUtilization",
            "value": value,
            "unit": "Percent",
        })
    db.table("resource_metrics").upsert(payload, on_conflict="resource_id,time,metric_name").execute()
    print(f"Injected {len(payload)} metric points. The detector should flag it on the next cycle.")
    print(f"Clean up afterwards with: --cleanup {resource['id']}")


def cleanup(db: Client, resource_id: str) -> None:
    since = (datetime.now(timezone.utc) - WINDOW).isoformat()
    res = (
        db.table("resource_metrics").delete()
        .eq("resource_id", resource_id).eq("metric_name", "CPUUtilization").gte("time", since)
        .execute()
    )
    print(f"Removed {len(res.data or [])} samples for {resource_id}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cleanup", metavar="RESOURCE_ID", help="remove the samples injected for this resource")
    args = parser.parse_args()
    client = _client()
    if args.cleanup:
        cleanup(client, args.cleanup)
    else:
        inject(client)
