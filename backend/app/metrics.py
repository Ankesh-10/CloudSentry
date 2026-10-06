"""Minimal Prometheus-format metrics without an extra dependency.

Counters and simple duration summaries (sum/count) keyed by name + labels.
Exposed at GET /metrics, which is disabled unless METRICS_TOKEN is set and then
requires `Authorization: Bearer <METRICS_TOKEN>`. Values are per process.
"""
import threading
from typing import Dict, Tuple

_lock = threading.Lock()
_counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
_summaries: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], list] = {}

HELP = {
    "cloudsentry_http_requests_total": "HTTP requests by method, route template and status class",
    "cloudsentry_http_request_duration_seconds": "HTTP request duration",
    "cloudsentry_actions_total": "Action outcomes by type and result",
    "cloudsentry_alerts_total": "Alert webhook deliveries",
    "cloudsentry_job_runs_total": "Scheduled job runs by job and result",
}


def _key(name: str, labels: dict) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
    return name, tuple(sorted((k, str(v)) for k, v in labels.items()))


def inc(name: str, value: float = 1.0, **labels) -> None:
    k = _key(name, labels)
    with _lock:
        _counters[k] = _counters.get(k, 0.0) + value


def observe(name: str, seconds: float, **labels) -> None:
    k = _key(name, labels)
    with _lock:
        s = _summaries.setdefault(k, [0.0, 0])
        s[0] += seconds
        s[1] += 1


def reset() -> None:
    with _lock:
        _counters.clear()
        _summaries.clear()


def _fmt_labels(labels: Tuple[Tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    esc = lambda v: v.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')  # noqa: E731
    return "{" + ",".join(f'{k}="{esc(v)}"' for k, v in labels) + "}"


def render() -> str:
    from backend.app.services import job_status
    lines = []
    with _lock:
        counters = dict(_counters)
        summaries = {k: list(v) for k, v in _summaries.items()}
    seen = set()
    for (name, labels), value in sorted(counters.items()):
        if name not in seen:
            lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} counter"]
            seen.add(name)
        lines.append(f"{name}{_fmt_labels(labels)} {value:g}")
    for (name, labels), (total, count) in sorted(summaries.items()):
        if name not in seen:
            lines += [f"# HELP {name} {HELP.get(name, name)}", f"# TYPE {name} summary"]
            seen.add(name)
        lines.append(f"{name}_sum{_fmt_labels(labels)} {total:.6f}")
        lines.append(f"{name}_count{_fmt_labels(labels)} {count}")
    jobs = job_status.snapshot()
    if jobs:
        lines += ["# HELP cloudsentry_job_consecutive_failures Consecutive failures per scheduled job",
                  "# TYPE cloudsentry_job_consecutive_failures gauge"]
        for job, st in sorted(jobs.items()):
            lines.append(f'cloudsentry_job_consecutive_failures{{job="{job}"}} {st.get("consecutive_failures", 0)}')
    return "\n".join(lines) + "\n"
