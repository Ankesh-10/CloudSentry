"""Runtime safety flags: env defaults, overlaid by system_config, mutable in-process."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from backend.app.config import settings

logger = logging.getLogger(__name__)

BOOL_KEYS = {"GLOBAL_AUTOMATION_ENABLED", "DRY_RUN_MODE"}
INT_KEYS = {"MAX_ACTIONS_PER_DAY", "ACTION_COOLDOWN_MINUTES", "MAX_CW_API_CALLS_PER_HOUR", "MAX_CW_METRICS_PER_HOUR"}
FLOAT_KEYS = {"MAX_MONTHLY_BUDGET_USD", "MAX_DAILY_SPEND_USD"}
KNOWN_KEYS = BOOL_KEYS | INT_KEYS | FLOAT_KEYS

# Inclusive upper bounds. A typo (an extra zero) must not silently remove a cap.
MAX_VALUES: dict[str, float] = {
    "MAX_ACTIONS_PER_DAY": 1_000,
    "ACTION_COOLDOWN_MINUTES": 7 * 24 * 60,
    "MAX_CW_API_CALLS_PER_HOUR": 100_000,
    "MAX_CW_METRICS_PER_HOUR": 1_000_000,
    "MAX_MONTHLY_BUDGET_USD": 1_000_000,
    "MAX_DAILY_SPEND_USD": 100_000,
}

# Unsafe-direction changes wait for a second operator; the request expires.
PENDING_PREFIX = "PENDING:"
PENDING_TTL = timedelta(hours=1)

_cache: dict[str, Any] = {}


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _parse(key: str, value: Any) -> Any:
    """Strict parse. Raises ValueError on anything ambiguous, negative,
    non-finite or above the key's upper bound."""
    if value is None:
        raise ValueError(f"{key} requires a value")
    if key in BOOL_KEYS:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueError(f"{key} must be true or false")
    if key in INT_KEYS:
        if isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        parsed = int(str(value).strip())
    elif key in FLOAT_KEYS:
        parsed = float(str(value).strip())
        # nan compares false against every cap, which would disable it.
        if not math.isfinite(parsed):
            raise ValueError(f"{key} must be a finite number")
    else:
        return str(value)
    if parsed < 0:
        raise ValueError(f"{key} must be >= 0")
    limit = MAX_VALUES.get(key)
    if limit is not None and parsed > limit:
        raise ValueError(f"{key} must be <= {limit:g}")
    return parsed


def validate(key: str, value: Any) -> Any:
    """Parse without applying. Raises ValueError for unknown keys or bad values."""
    if key not in KNOWN_KEYS:
        raise ValueError(f"Unknown configuration key: {key}")
    return _parse(key, value)


def _is_safer(key: str, value: Any) -> bool:
    """Changes that can only reduce what the agent does to the cloud."""
    return (key == "GLOBAL_AUTOMATION_ENABLED" and value is False) or (key == "DRY_RUN_MODE" and value is True)


# Caps that allow more when raised / when lowered. Loosening one is as risky as
# enabling automation: one operator could otherwise lift the daily action cap
# to 1000 or the budget to $1M alone.
LOOSER_WHEN_HIGHER = {"MAX_ACTIONS_PER_DAY", "MAX_CW_API_CALLS_PER_HOUR", "MAX_CW_METRICS_PER_HOUR",
                      "MAX_MONTHLY_BUDGET_USD", "MAX_DAILY_SPEND_USD"}
LOOSER_WHEN_LOWER = {"ACTION_COOLDOWN_MINUTES"}


def _is_unsafe(key: str, value: Any) -> bool:
    """Changes that let the agent mutate real cloud resources, or do more of it."""
    if key == "GLOBAL_AUTOMATION_ENABLED":
        return value is True
    if key == "DRY_RUN_MODE":
        return value is False
    current = get_flag(key)
    if current is None:
        return True
    if key in LOOSER_WHEN_HIGHER:
        return value > current
    if key in LOOSER_WHEN_LOWER:
        return value < current
    return False


def load_from_env() -> None:
    """Seed the cache from settings. Invalid env values (nan, out of range)
    fail startup rather than run with a disabled cap."""
    _cache.update({key: _parse(key, getattr(settings, key)) for key in KNOWN_KEYS})


def refresh_from_db() -> bool:
    """Overlay system_config onto the cache. Returns False if the DB could not
    be read (callers that are about to mutate the cloud must then stop)."""
    try:
        from backend.app.db.supabase_client import get_supabase_client
        db = get_supabase_client()
        res = db.table("system_config").select("key, value").execute()
    except Exception as e:
        logger.warning("Could not refresh system_config from database: %s", e)
        return False
    for row in res.data or []:
        key = row.get("key")
        if key not in KNOWN_KEYS:
            continue
        try:
            _cache[key] = _parse(key, row.get("value"))
        except (TypeError, ValueError):
            logger.error("Invalid system_config value for %s: %r", key, row.get("value"))
            if key == "GLOBAL_AUTOMATION_ENABLED":
                _cache[key] = False
            elif key == "DRY_RUN_MODE":
                _cache[key] = True
    return True


def get_flag(key: str, default: Any = None) -> Any:
    if key in _cache:
        return _cache[key]
    return getattr(settings, key, default)


def snapshot() -> dict[str, Any]:
    if not _cache:
        load_from_env()
    return dict(_cache)


def set_flag(key: str, value: Any, persist: bool = True) -> Any:
    if key not in KNOWN_KEYS:
        raise ValueError(f"Unknown configuration key: {key}")
    parsed = _parse(key, value)
    # Safe-direction changes take effect in-process even if persisting fails;
    # unsafe-direction changes only take effect once durably stored.
    if not persist or _is_safer(key, parsed):
        _cache[key] = parsed
    if persist:
        try:
            from backend.app.db.supabase_client import get_supabase_client
            db = get_supabase_client()
            db.table("system_config").upsert({
                "key": key,
                "value": str(parsed).lower() if isinstance(parsed, bool) else str(parsed),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="key").execute()
        except Exception as e:
            logger.error("Failed to persist system_config %s: %s", key, e)
            raise
        _cache[key] = parsed
    return parsed


def _pending_request(db, key: str, parsed: Any) -> Optional[dict]:
    """The live (unexpired, same-value) pending request for key, if any."""
    res = db.table("system_config").select("key, value").eq("key", PENDING_PREFIX + key).execute()
    if not res.data:
        return None
    try:
        pending = json.loads(res.data[0].get("value") or "")
        requested_at = datetime.fromisoformat(pending["requested_at"])
        if _parse(key, pending["value"]) != parsed:
            return None
    except (TypeError, ValueError, KeyError):
        return None
    if datetime.now(timezone.utc) - requested_at > PENDING_TTL:
        return None
    return pending


def _clear_pending(db, key: str) -> None:
    db.table("system_config").delete().eq("key", PENDING_PREFIX + key).execute()


def request_change(key: str, value: Any, actor: str) -> dict[str, Any]:
    """Apply a config change, requiring a second operator for unsafe ones.

    Returns {"status": "applied", "value": ...} or
    {"status": "pending_confirmation", ...}. Raises ValueError on bad input,
    PermissionError when the requester tries to confirm their own request, and
    any database error unchanged (the change is then not applied)."""
    if key not in KNOWN_KEYS:
        raise ValueError(f"Unknown configuration key: {key}")
    parsed = _parse(key, value)
    if not settings.REQUIRE_TWO_PERSON_CONFIG or not _is_unsafe(key, parsed) or get_flag(key) == parsed:
        set_flag(key, parsed, persist=True)
        return {"status": "applied", "value": parsed}

    from backend.app.db.supabase_client import get_supabase_client
    db = get_supabase_client()
    pending = _pending_request(db, key, parsed)
    if pending is None:
        now = datetime.now(timezone.utc)
        db.table("system_config").upsert({
            "key": PENDING_PREFIX + key,
            "value": json.dumps({"value": str(parsed).lower(), "requested_by": actor,
                                 "requested_at": now.isoformat()}),
            "updated_at": now.isoformat(),
        }, on_conflict="key").execute()
        return {"status": "pending_confirmation", "value": parsed, "requested_by": actor,
                "expires_at": (now + PENDING_TTL).isoformat()}
    if pending.get("requested_by") == actor:
        raise PermissionError("A different operator must confirm this change")
    set_flag(key, parsed, persist=True)
    _clear_pending(db, key)
    return {"status": "applied", "value": parsed, "requested_by": pending.get("requested_by"),
            "confirmed_by": actor}


def emergency_stop() -> tuple[dict[str, Any], bool]:
    """Disable automation. Always effective in this process; returns whether the
    database copy was also updated (other replicas read it before acting)."""
    persisted = True
    try:
        set_flag("GLOBAL_AUTOMATION_ENABLED", False, persist=True)
    except Exception:
        persisted = False
    # A half-approved request to re-enable must not survive the stop.
    try:
        from backend.app.db.supabase_client import get_supabase_client
        _clear_pending(get_supabase_client(), "GLOBAL_AUTOMATION_ENABLED")
    except Exception as e:
        logger.warning("Emergency stop could not clear pending enable request: %s", e)
    _cache["GLOBAL_AUTOMATION_ENABLED"] = False
    logger.warning("Emergency stop: GLOBAL_AUTOMATION_ENABLED set to false (persisted=%s)", persisted)
    return snapshot(), persisted


def automation_enabled() -> bool:
    return bool(get_flag("GLOBAL_AUTOMATION_ENABLED", False))


def dry_run_mode() -> bool:
    return bool(get_flag("DRY_RUN_MODE", True))


load_from_env()
