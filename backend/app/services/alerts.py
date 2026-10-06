"""Operator alerts via an incoming webhook (Slack-compatible {"text": ...}).

Disabled unless ALERT_WEBHOOK_URL is set. The URL comes only from server
configuration, never from a request. Sending is best-effort and synchronous
with a short timeout; callers already run off the event loop (scheduler
executor threads, FastAPI's threadpool). Repeats of the same alert key are
suppressed for ALERT_MIN_INTERVAL_SECONDS so a flapping job cannot flood the
channel.
"""
import logging
import threading
import time
from typing import Optional

import httpx

from backend.app.config import settings
from backend.app.logging_config import scrub

logger = logging.getLogger(__name__)

JOB_FAILURE_ALERT = 3

_last_sent: dict[str, float] = {}
_lock = threading.Lock()


def reset() -> None:
    with _lock:
        _last_sent.clear()


def _throttled(key: str) -> bool:
    now = time.monotonic()
    with _lock:
        last = _last_sent.get(key)
        if last is not None and now - last < settings.ALERT_MIN_INTERVAL_SECONDS:
            return True
        _last_sent[key] = now
        return False


def send(key: str, text: str) -> bool:
    """Returns True if the alert was delivered."""
    url = settings.ALERT_WEBHOOK_URL
    if not url:
        return False
    if _throttled(key):
        return False
    from backend.app import metrics
    body = {"text": scrub(f"[CloudSentry/{settings.ENVIRONMENT}] {text}")[:3000]}
    try:
        resp = httpx.post(url, json=body, timeout=5.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("Alert delivery failed for %s: %s", key, type(e).__name__)
        metrics.inc("cloudsentry_alerts_total", result="failed")
        return False
    metrics.inc("cloudsentry_alerts_total", result="sent")
    return True


def job_failing(job: str, entry: dict) -> Optional[bool]:
    failures = int(entry.get("consecutive_failures") or 0)
    if failures < JOB_FAILURE_ALERT:
        return None
    return send(f"job:{job}", f":rotating_light: Job `{job}` has failed {failures} times in a row: "
                              f"{entry.get('last_error') or 'no error text'}")


def action_failed(action: dict, resource: dict, message: str) -> bool:
    return send(
        f"action:{action.get('id')}",
        f":x: Action `{action.get('action_type')}` on `{resource.get('provider_id')}` failed: {message}",
    )


def verification_timeout(action: dict, resource: dict, observed) -> bool:
    return send(
        f"verify:{action.get('id')}",
        f":warning: `{action.get('action_type')}` on `{resource.get('provider_id')}` did not reach its target "
        f"state (observed: {observed}). Check the resource manually.",
    )


def rollback_failed(action_id: str, resource: dict, message: str) -> bool:
    return send(f"rollback:{action_id}",
                f":x: Rollback of action {action_id} on `{resource.get('provider_id')}` failed: {message}")


def emergency_stop(actor: str, persisted: bool) -> bool:
    note = "" if persisted else " (NOT persisted to the database: other replicas may still be running!)"
    return send(f"estop:{time.time()}", f":octagonal_sign: Emergency stop by {actor}{note}")
