import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from backend.app.adapters.base import CloudProviderError
from backend.app.adapters.factory import get_cloud_adapter
from backend.app.config import settings
from backend.app.db.pagination import fetch_all
from backend.app.db.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

GONE_STATES = {"terminated", "deleted"}


class DiscoveryError(Exception):
    def __init__(self, message: str, summary: Dict[str, Any]):
        super().__init__(message)
        self.summary = summary


class DiscoveryService:
    def __init__(self):
        self.cloud_adapter = get_cloud_adapter()
        self.db = get_supabase_client()

    def _get_or_create_account(self) -> str:
        res = self.db.table("cloud_accounts").select("id").limit(1).execute()
        if res.data:
            return res.data[0]["id"]

        new_account = {
            "provider": settings.CLOUD_PROVIDER,
            "account_id": settings.CLOUD_ACCOUNT_ID or "unknown",
            "region": settings.AWS_DEFAULT_REGION,
        }
        res = self.db.table("cloud_accounts").insert(new_account).execute()
        return res.data[0]["id"]

    def _sync_resources(self, account_id: str, resource_type: str, discovered: List[Dict[str, Any]]) -> Dict[str, int]:
        now = datetime.now(timezone.utc).isoformat()
        existing = fetch_all(
            lambda: self.db.table("resources")
            .select("id, provider_id, state")
            .eq("resource_type", resource_type)
            .order("id")
        )
        existing_map = {r["provider_id"]: r for r in existing}

        # New and existing rows are written separately with uniform keys:
        # supabase-py bulk writes fill keys missing from some rows with NULL,
        # which would null `id` on inserts and `first_seen` on updates.
        inserts: List[Dict[str, Any]] = []
        updates: List[Dict[str, Any]] = []
        audit_payload = []
        seen_ids = set()

        for res in discovered:
            provider_id = res.get("id")
            if not provider_id or provider_id in seen_ids:
                continue
            seen_ids.add(provider_id)
            state = res.get("state")
            payload = {
                "account_id": account_id,
                "provider_id": provider_id,
                "resource_type": resource_type,
                "name": res.get("name"),
                "region": res.get("region"),
                "state": state,
                "tags": res.get("tags") or {},
                "metadata": res.get("metadata") or {},
                "last_seen": now,
            }
            if provider_id in existing_map:
                old = existing_map[provider_id]
                if old["state"] != state:
                    audit_payload.append({
                        "event_type": "state_change",
                        "actor": "SYSTEM",
                        "resource_id": old["id"],
                        "message": f"State changed from {old['state']} to {state}",
                    })
                updates.append(payload)
            else:
                payload["first_seen"] = now
                inserts.append(payload)

        conflict = "resource_type,provider_id"
        if inserts:
            self.db.table("resources").upsert(inserts, on_conflict=conflict).execute()
        if updates:
            self.db.table("resources").upsert(updates, on_conflict=conflict).execute()

        # Resources that disappeared from a *successful* listing no longer exist
        # (terminated instances age out of DescribeInstances, deleted buckets...).
        gone = [
            row for pid, row in existing_map.items()
            if pid not in seen_ids and row.get("state") not in GONE_STATES
        ]
        for row in gone:
            self.db.table("resources").update({"state": "deleted"}).eq("id", row["id"]).execute()
            audit_payload.append({
                "event_type": "state_change",
                "actor": "SYSTEM",
                "resource_id": row["id"],
                "message": f"State changed from {row['state']} to deleted (no longer returned by provider)",
            })

        if audit_payload:
            self.db.table("audit_logs").insert(audit_payload).execute()

        return {"inserted": len(inserts), "updated": len(updates), "deleted": len(gone)}

    def run(self) -> Dict[str, Any]:
        logger.info("Starting Resource Discovery cycle...")
        account_id = self._get_or_create_account()
        summary: Dict[str, Any] = {"errors": []}

        type_map = [
            ("ec2", self.cloud_adapter.discover_instances),
            ("lambda", self.cloud_adapter.discover_functions),
            ("s3", self.cloud_adapter.discover_buckets),
            ("rds", self.cloud_adapter.discover_databases),
            ("ebs", self.cloud_adapter.discover_volumes),
        ]

        for resource_type, fetcher in type_map:
            try:
                discovered = fetcher()
                summary[resource_type] = self._sync_resources(account_id, resource_type, discovered)
            except CloudProviderError as e:
                logger.error("Discovery failed for %s: %s", resource_type, e)
                summary["errors"].append(resource_type)
            except Exception as e:
                logger.error("Unexpected discovery error for %s: %s", resource_type, e)
                summary["errors"].append(resource_type)

        logger.info("Resource Discovery cycle completed: %s", summary)
        if summary["errors"]:
            raise DiscoveryError(f"Discovery failed for: {', '.join(summary['errors'])}", summary)
        return summary
