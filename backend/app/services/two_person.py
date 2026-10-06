"""Second-operator confirmation for API changes that let the agent do more.

Shared by policy toggles and resource protection (runtime config keeps its own
flow in runtime_config.request_change). A request is stored in system_config
under PENDING:<key>, so GET /system/config/pending lists it and any operator
can cancel it; it expires after PENDING_TTL. A different operator repeating
the same change confirms it.

Callers audit before each step: live_request() to see whether this call
requests or confirms, then audit, then record_request() / apply + clear().
"""
import json
from datetime import datetime, timezone
from typing import Any, Optional

from backend.app.db.supabase_client import get_supabase_client
from backend.app.services.runtime_config import PENDING_PREFIX, PENDING_TTL


def live_request(key: str, value: Any) -> Optional[dict]:
    """The unexpired request for exactly this change, if any."""
    res = get_supabase_client().table("system_config").select("key, value").eq("key", PENDING_PREFIX + key).execute()
    if not res.data:
        return None
    try:
        pending = json.loads(res.data[0].get("value") or "")
        requested_at = datetime.fromisoformat(pending["requested_at"])
    except (TypeError, ValueError, KeyError):
        return None
    if pending.get("value") != value or datetime.now(timezone.utc) - requested_at > PENDING_TTL:
        return None
    return pending


def record_request(key: str, value: Any, actor: str) -> str:
    """Store a new request; returns when it expires (ISO 8601, UTC)."""
    now = datetime.now(timezone.utc)
    get_supabase_client().table("system_config").upsert({
        "key": PENDING_PREFIX + key,
        "value": json.dumps({"value": value, "requested_by": actor, "requested_at": now.isoformat()}),
        "updated_at": now.isoformat(),
    }, on_conflict="key").execute()
    return (now + PENDING_TTL).isoformat()


def clear(key: str) -> None:
    get_supabase_client().table("system_config").delete().eq("key", PENDING_PREFIX + key).execute()
