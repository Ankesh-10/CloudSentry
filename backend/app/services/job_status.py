"""In-process record of scheduled job outcomes, surfaced by /health."""
import threading
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()
_status: dict[str, dict[str, Any]] = {}


def record(job: str, ok: bool, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        entry = _status.setdefault(
            job, {"last_success": None, "last_failure": None, "last_error": None, "consecutive_failures": 0}
        )
        if ok:
            entry["last_success"] = now
            entry["consecutive_failures"] = 0
        else:
            entry["last_failure"] = now
            entry["last_error"] = (error or "")[:300]
            entry["consecutive_failures"] += 1


def snapshot() -> dict[str, dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _status.items()}
