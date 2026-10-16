"""Live, switchable synthetic telemetry for demos.

Seeds a set of demo resources, backfills history, then writes a sample for
every metric every few seconds. Every situation CloudSentry can detect or act
on is a *scenario* you switch on and off while it runs. Each scenario drives
its own resource, so any combination can be on at once. Charts pick a change up
within seconds; the detector and policy engine on their next pipeline cycle.

Writes fake rows with the service-role key, so it refuses to run unless you
opt in explicitly. Demo/dev databases only:

    CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS=1 python scripts/telemetry_demo.py start [--story]
    python scripts/telemetry_demo.py list                    # scenarios, what each shows
    python scripts/telemetry_demo.py on cpu-spike idle-ec2   # from another terminal
    python scripts/telemetry_demo.py toggle untagged
    python scripts/telemetry_demo.py off all
    CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS=1 python scripts/telemetry_demo.py train-ml
    CLOUDSENTRY_ALLOW_SYNTHETIC_METRICS=1 python scripts/telemetry_demo.py cleanup

While `start` runs, the same commands can be typed into its terminal
(`on cpu-spike`, `off all`, `toggle idle-ec2`, `list`, `quit`). `on all`
switches on everything except `stopped` and `outage`, which would hide the
others.

`--story` plays a narrated ~20 minute timeline through every scenario by
itself, so a live demo needs no typing; on/off still work while it plays.

Samples go out every --interval seconds (default 10) so charts move, but each
lands in a --bucket-seconds slot (default 30): later writes in the same slot
overwrite it. The detector counts samples, not minutes, and was tuned for
CloudWatch's 5-minute period; at one sample per 10 s a sustained spike would
become "normal" within a minute or two, and the EWMA fallback would flag only
the first 10-30 s of it, which a once-a-minute pipeline usually misses.

Safety: the rows carry metadata.synthetic=true, which makes the backend force
every action on them to dry-run whatever DRY_RUN_MODE says, and their provider
ids are not valid cloud ids. Most are unprotected, so policies really propose
actions you can approve; executing one still needs GLOBAL_AUTOMATION_ENABLED=true
(otherwise it shows as blocked by the kill-switch, which is a scenario too).

Backend settings for a quick demo: JOB_PIPELINE_MINUTES=1, ML_IDLE_WINDOW_HOURS=0.1
(idle must last the whole window: 6 min) and, for `ml-anomaly`,
ML_IF_DECISION_THRESHOLD=-0.05: at the default 0 the model calls ~5% of normal
samples anomalous by design, so demo-ml-api raises a false alarm in about a
third of 20-minute runs; at -0.05, about one in ten, and the flood is still
always caught.
"""
import argparse
import json
import os
import queue
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / ".telemetry_demo.json"
DEMO_ACCOUNT = "demo-telemetry"
DEMO_TAGS = {"Project": "cloudsentry-demo", "Owner": "demo", "cloudsentry:demo": "telemetry"}
ML_HISTORY_HOURS = 8 * 24  # >= ML_MIN_POINTS_IF (2016) samples at 5 min, so the Isolation Forest path runs
DEFAULT_BUCKET_SECONDS = 30
BASE_STATE = {"ec2": "running", "lambda": "available", "rds": "available", "ebs": "in-use"}


@dataclass
class DemoResource:
    rtype: str
    provider_id: str  # never a real id: "demo" is not hex, so no cloud can have it
    name: str
    protected: bool = False
    tags: dict = field(default_factory=dict)  # on top of the required/demo tags
    metadata: dict = field(default_factory=dict)  # instance_type etc., for cost estimates
    history_hours: float | None = None  # None: --history-hours


RESOURCES = {
    "web": DemoResource("ec2", "i-0demo00000000web", "demo-web-api", metadata={"instance_type": "t3.small"}),
    "worker": DemoResource("ec2", "i-0demo000000worker", "demo-batch-worker", metadata={"instance_type": "t3.small"}),
    "bastion": DemoResource("ec2", "i-0demo00000bastion", "demo-bastion", protected=True,
                            metadata={"instance_type": "t3.micro"}),
    "asg-node": DemoResource("ec2", "i-0demo0000asgnode", "demo-asg-node",
                             tags={"aws:autoscaling:groupName": "demo-web-asg"}, metadata={"instance_type": "t3.small"}),
    "build-agent": DemoResource("ec2", "i-0demo00buildagent", "demo-build-agent", tags={"do-not-stop": "true"},
                                metadata={"instance_type": "t3.small"}),
    "legacy": DemoResource("ec2", "i-0demo00000legacy", "demo-legacy-vm", metadata={"instance_type": "t2.micro"}),
    "ml": DemoResource("ec2", "i-0demo000000mlapi", "demo-ml-api", metadata={"instance_type": "t3.small"},
                       history_hours=ML_HISTORY_HOURS),
    "thumbnailer": DemoResource("lambda", "demo-thumbnailer", "demo-thumbnailer", metadata={"memory": 512}),
    "webhook": DemoResource("lambda", "demo-invoice-webhook", "demo-invoice-webhook", metadata={"memory": 128}),
    "notifier": DemoResource("lambda", "demo-notifier", "demo-notifier", metadata={"memory": 128}),
    "db": DemoResource("rds", "demo-orders-db", "demo-orders-db", protected=True,
                       metadata={"instance_class": "db.t3.micro", "allocated_storage_gb": 20}),
    "reporting-db": DemoResource("rds", "demo-reporting-db", "demo-reporting-db",
                                 metadata={"instance_class": "db.t3.micro", "allocated_storage_gb": 20}),
    "analytics-db": DemoResource("rds", "demo-analytics-db", "demo-analytics-db",
                                 metadata={"instance_class": "db.t3.micro", "allocated_storage_gb": 20}),
    "scratch": DemoResource("ebs", "vol-0demo00scratch", "demo-scratch-volume",
                            metadata={"volume_type": "gp3", "size": 100}),
}

UNITS = {
    "CPUUtilization": "Percent", "NetworkIn": "Bytes", "NetworkOut": "Bytes",
    "Invocations": "Count", "Duration": "Milliseconds", "Errors": "Count",
    "DatabaseConnections": "Count", "FreeStorageSpace": "Bytes",
}

# resource_type -> mode -> metric -> (low, high). EBS volumes have no metrics.
PROFILES = {
    "ec2": {
        "normal": {"CPUUtilization": (18, 32), "NetworkIn": (2e6, 5e6), "NetworkOut": (1e6, 3e6)},
        "spike": {"CPUUtilization": (92, 99.5), "NetworkIn": (20e6, 40e6), "NetworkOut": (15e6, 30e6)},
        "idle": {"CPUUtilization": (0.2, 1.5), "NetworkIn": (0, 800), "NetworkOut": (0, 800)},
        # Exfiltration-like: the box goes quiet while traffic floods out. The
        # Isolation Forest flags the combination reliably; a network flood
        # alone at normal CPU it mostly does not.
        "quiet-flood": {"CPUUtilization": (2, 4), "NetworkIn": (60e6, 90e6), "NetworkOut": (40e6, 60e6)},
    },
    "lambda": {
        "normal": {"Invocations": (40, 80), "Duration": (120, 220), "Errors": (0, 1)},
        # >= 15x the normal rate, so the anomaly clears the 0.9 confidence the
        # "Limit runaway Lambda concurrency" policy asks for.
        "runaway": {"Invocations": (1500, 2500), "Duration": (300, 600), "Errors": (5, 20)},
        # Error rate > 20% at normal volume: runaway_lambda, but confidence ~0.6.
        "errors": {"Invocations": (40, 80), "Duration": (800, 1500), "Errors": (25, 50)},
        # ~4x: a statistical outlier, well under the 10x runaway rule.
        "surge": {"Invocations": (200, 320), "Duration": (120, 220), "Errors": (0, 1)},
    },
    "rds": {
        "normal": {"DatabaseConnections": (8, 20), "FreeStorageSpace": (14.0e9, 14.2e9)},
        "busy": {"DatabaseConnections": (300, 400), "FreeStorageSpace": (12.0e9, 12.2e9)},
        "idle": {"DatabaseConnections": (0, 0), "FreeStorageSpace": (14.0e9, 14.2e9)},
        # ~5-6% of the 20 GiB allocated: under RDS_LOW_STORAGE_PCT (10).
        "storage-low": {"DatabaseConnections": (8, 20), "FreeStorageSpace": (1.0e9, 1.4e9)},
    },
}


@dataclass
class Scenario:
    resource: str  # key in RESOURCES, or "*" for all of them
    shows: str  # what CloudSentry does about it
    wait: str  # roughly how long until it shows (JOB_PIPELINE_MINUTES=1, 30 s slots)
    mode: str | None = None  # metric profile while on
    state: str | None = None  # resource state while on
    silent: bool = False  # write no samples while on
    untag: bool = False  # drop the required tags while on
    masks: bool = False  # hides other scenarios: left out of `on all`


SCENARIOS = {
    "cpu-spike": Scenario(
        "web", mode="spike", wait="1-2 min",
        shows="unusual_cpu_spike (Z-score, then EWMA). No policy matches: alert only."),
    "idle-ec2": Scenario(
        "worker", mode="idle", wait="~7 min",
        shows="idle_compute -> stop_ec2 from 'Auto-stop idle EC2' (runs as a dry run)."),
    "idle-protected": Scenario(
        "bastion", mode="idle", wait="~7 min",
        shows="idle_compute, no action: the resource is protected."),
    "idle-autoscaled": Scenario(
        "asg-node", mode="idle", wait="~7 min",
        shows="idle_compute, no action: stopping an Auto Scaling member would replace it."),
    "idle-do-not-stop": Scenario(
        "build-agent", mode="idle", wait="~7 min",
        shows="idle_compute, no action: the instance has a do-not-stop tag."),
    "stopped": Scenario(
        "worker", state="stopped", silent=True, masks=True, wait="next cycle",
        shows="demo-batch-worker stopped by hand: no samples; its idle/behaviour anomalies resolve."),
    "untagged": Scenario(
        "legacy", untag=True, wait="next cycle",
        shows="untagged_resource -> apply_tags (auto; dry run, so the anomaly stays)."),
    "unused-volume": Scenario(
        "scratch", state="available", wait="next cycle",
        shows="unused_volume -> recommend_review waiting for approval (never deletes)."),
    "lambda-runaway": Scenario(
        "thumbnailer", mode="runaway", wait="1-2 min",
        shows="runaway_lambda (HIGH) -> limit_lambda (auto; dry run)."),
    "lambda-errors": Scenario(
        "webhook", mode="errors", wait="1-2 min",
        shows="runaway_lambda from a >20% error rate; confidence under the policy's 0.9: no action."),
    "lambda-surge": Scenario(
        "notifier", mode="surge", wait="1-2 min",
        shows="~4x invocations: statistical_anomaly (under the 10x runaway rule). Alert only."),
    "rds-busy": Scenario(
        "db", mode="busy", wait="1-2 min",
        shows="connections x20: statistical_anomaly. The RDS is protected: alert only."),
    "rds-idle": Scenario(
        "reporting-db", mode="idle", wait="~7 min",
        shows="no connections for the idle window: idle_database -> recommend_review (needs approval)."),
    "rds-storage": Scenario(
        "analytics-db", mode="storage-low", wait="next cycle",
        shows="free storage ~6% of 20 GiB: low_storage -> recommend_review (needs approval)."),
    "ml-anomaly": Scenario(
        "ml", mode="quiet-flood", wait="1-2 min",
        shows="CPU ~3% while network floods: ml_behavioral_anomaly once `train-ml` ran (best with "
              "ML_IF_DECISION_THRESHOLD=-0.05) "
              "(without a model only EWMA looks, and it flags the CPU drop for a minute at most)."),
    "outage": Scenario(
        "*", silent=True, masks=True, wait="30 min",
        shows="no demo samples: metric rules stop evaluating; /system/health reports "
              "telemetry_stale_resources (each demo EC2/RDS), and telemetry_stale if nothing else reports."),
}

# (minute, [(on|off, scenario|all)], what to point at). Assumes the backend runs
# with JOB_PIPELINE_MINUTES=1 and ML_IDLE_WINDOW_HOURS=0.1. Idle goes first: it
# has to last the whole 6-minute window, so it is the slowest to show.
STORY = [
    (0, [("off", "all")],
     "Baseline: 14 healthy demo resources. Open the Dashboard and a resource page."),
    (1, [("on", "idle-ec2"), ("on", "idle-protected"), ("on", "idle-autoscaled"), ("on", "idle-do-not-stop")],
     "Four instances go idle. In ~7 min all four get idle_compute, but only demo-batch-worker gets a "
     "stop: protection, the Auto Scaling tag and do-not-stop hold back the other three."),
    (2, [("on", "cpu-spike")],
     "demo-web-api CPU jumps to ~95%: unusual_cpu_spike in 1-2 min. No policy, so alert only."),
    (4, [("on", "lambda-runaway")],
     "demo-thumbnailer runs away (~30x invocations): runaway_lambda (HIGH) and a limit_lambda action."),
    (6, [("on", "untagged"), ("on", "unused-volume")],
     "Rule checks: demo-legacy-vm loses its tags (apply_tags) and demo-scratch-volume is detached "
     "(recommend_review, waits for approval)."),
    (9, [],
     "Anomalies page: four idle_compute rows. Actions page: a stop only for demo-batch-worker."),
    (10, [("off", "cpu-spike"), ("off", "lambda-runaway"), ("on", "lambda-errors"), ("on", "lambda-surge")],
     "Spike and runaway end (resolved after 30 quiet min). demo-invoice-webhook starts failing (anomaly, "
     "too little confidence for an action); demo-notifier surges ~4x (statistical_anomaly)."),
    (13, [("on", "rds-busy"), ("on", "rds-idle"), ("on", "rds-storage"), ("on", "ml-anomaly")],
     "Databases: demo-orders-db gets busy (protected: alert only), demo-reporting-db goes unused and "
     "demo-analytics-db runs out of space (both get a review to approve). demo-ml-api goes quiet while its "
     "network floods: ml_behavioral_anomaly if you ran `train-ml`."),
    (16, [],
     "Actions page: the idle stop and Lambda limit ran as dry runs ('Synthetic demo resource: WOULD ...'), "
     "or show as blocked if the kill-switch is on. Approve the volume review; then check the Audit log."),
    (18, [("on", "stopped")],
     "demo-batch-worker is stopped by hand: its idle anomaly resolves on the next cycle."),
    (20, [("off", "all")],
     "Recovery: everything back to normal. Behavioural anomalies resolve after 30 quiet minutes."),
]


# --- scenario state (shared between `start` and on/off via a small JSON file) ---

def read_active() -> set:
    try:
        data = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return set()
    on = data.get("on") if isinstance(data, dict) else None
    return {n for n in on if n in SCENARIOS} if isinstance(on, list) else set()


def write_active(active: set) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"on": sorted(active)}, indent=2))
    os.replace(tmp, STATE_FILE)


def change(action: str, names: list) -> str:
    """on / off / toggle scenarios by name; `all` expands (see module doc)."""
    if action not in ("on", "off", "toggle"):
        return f"unknown command {action!r}; use on, off or toggle"
    if not names:
        return f"usage: {action} <scenario ...|all>"
    if "all" in names:
        names = [n for n, s in SCENARIOS.items() if action == "off" or not s.masks]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        return f"unknown scenario(s): {', '.join(unknown)}. Run `list` to see them."
    active = read_active()
    for name in names:
        if action == "on" or (action == "toggle" and name not in active):
            active.add(name)
        else:
            active.discard(name)
    write_active(active)
    return "on: " + (", ".join(sorted(active)) or "nothing (all normal)")


def required_tags() -> dict:
    """Tags every healthy demo resource carries, so only `untagged` trips the
    untagged rule even with a custom REQUIRED_TAGS."""
    names = [t.strip() for t in os.environ.get("REQUIRED_TAGS", "Project,Owner").split(",") if t.strip()]
    return {t: DEMO_TAGS.get(t, "demo") for t in names}


def resource_tags(key: str, untag: bool = False) -> dict:
    required = required_tags()
    tags = {**required, **DEMO_TAGS, **RESOURCES[key].tags}
    return {k: v for k, v in tags.items() if k not in required} if untag else tags


def desired(active: set) -> dict:
    """key -> {mode, state, silent, untag} for this set of scenarios."""
    out = {key: {"mode": "normal", "state": BASE_STATE[r.rtype], "silent": False, "untag": False}
           for key, r in RESOURCES.items()}
    for name in sorted(active):
        sc = SCENARIOS[name]
        for key in (RESOURCES if sc.resource == "*" else [sc.resource]):
            want = out[key]
            if sc.mode:
                want["mode"] = sc.mode
            if sc.state:
                want["state"] = sc.state
            want["silent"] = want["silent"] or sc.silent
            want["untag"] = want["untag"] or sc.untag
    return out


def format_list(active: set) -> str:
    lines = []
    for name, sc in SCENARIOS.items():
        target = "all demo resources" if sc.resource == "*" else \
            f"{RESOURCES[sc.resource].name} ({RESOURCES[sc.resource].rtype})"
        mark = "ON " if name in active else "off"
        lines.append(f"  [{mark}] {name:<17} {target:<29} {sc.wait:<15} {sc.shows}")
    return "\n".join(lines)


# --- database ---

def _client():
    from supabase import create_client

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


def _region() -> str:
    return os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def _demo_rows(db) -> list:
    ids = [r.provider_id for r in RESOURCES.values()]
    return (db.table("resources").select("id, provider_id, resource_type, state")
            .in_("provider_id", ids).eq("region", _region()).execute().data or [])


def _ids_by_key(db) -> dict:
    by_pid = {(r["resource_type"], r["provider_id"]): r["id"] for r in _demo_rows(db)}
    return {key: by_pid[(r.rtype, r.provider_id)] for key, r in RESOURCES.items()
            if (r.rtype, r.provider_id) in by_pid}


def seed(db, history_hours: float, want: dict) -> dict:
    """Create (or revive) the demo resources. Returns key -> resource uuid."""
    region = _region()
    acc = (db.table("cloud_accounts").select("id")
           .eq("provider", "aws").eq("account_id", DEMO_ACCOUNT).limit(1).execute().data)
    account_id = acc[0]["id"] if acc else db.table("cloud_accounts").insert(
        {"provider": "aws", "account_id": DEMO_ACCOUNT, "alias": "Telemetry demo", "region": region}
    ).execute().data[0]["id"]

    now = datetime.now(timezone.utc)
    rows = []
    for key, r in RESOURCES.items():
        # As old as its backfilled history, so age-gated policies (the idle
        # stop needs 30 min) can match during the demo.
        first_seen = now - timedelta(hours=max(r.history_hours or history_hours, 1))
        rows.append({
            "account_id": account_id, "provider_id": r.provider_id, "resource_type": r.rtype, "name": r.name,
            "region": region, "state": want[key]["state"], "tags": resource_tags(key, want[key]["untag"]),
            "protected": r.protected,
            # synthetic: the backend forces every action on this row to dry-run.
            "metadata": {**r.metadata, "synthetic": True},
            "first_seen": first_seen.isoformat(), "last_seen": now.isoformat(),
        })
    db.table("resources").upsert(rows, on_conflict="resource_type,region,provider_id").execute()
    return _ids_by_key(db)


class Generator:
    """Each metric's level glides to the middle of its mode's range (a few
    samples after a switch), and every sample adds uniform noise of half the
    range. Uniform noise stays within ~1.7 standard deviations, so a steady
    mode never trips the detector's 2.5-sigma tests by chance; smoothed
    (bell-shaped) noise did, about once per 200 resource-minutes."""

    def __init__(self):
        self.level: dict = {}

    def sample(self, key: str, rtype: str, mode: str) -> dict:
        out = {}
        for metric, (lo, hi) in PROFILES[rtype][mode].items():
            mid, half = (lo + hi) / 2, (hi - lo) / 2
            level = self.level.get((key, metric), mid) * 0.4 + mid * 0.6
            self.level[(key, metric)] = level
            out[metric] = round(max(0.0, level + random.uniform(-half, half)), 2)
        return out


def _rows(resource_id: str, at: datetime, values: dict) -> list:
    return [{"time": at.isoformat(), "resource_id": resource_id, "metric_name": m,
             "value": v, "unit": UNITS[m]} for m, v in values.items()]


def _upsert_metrics(db, rows: list) -> None:
    for i in range(0, len(rows), 1000):
        db.table("resource_metrics").upsert(
            rows[i:i + 1000], on_conflict="resource_id,time,metric_name").execute()


def backfill(db, ids: dict, gen: Generator, hours: float) -> None:
    """Normal history at CloudWatch's 5-minute period, only for resources that
    have none, so the detector has a baseline to compare live samples against."""
    now = datetime.now(timezone.utc)
    total = 0
    for key, rid in ids.items():
        r = RESOURCES[key]
        if r.rtype not in PROFILES:
            continue
        if db.table("resource_metrics").select("id").eq("resource_id", rid).limit(1).execute().data:
            continue
        rows = []
        for i in range(int((r.history_hours or hours) * 12), 0, -1):
            rows += _rows(rid, now - timedelta(minutes=5 * i), gen.sample(key, r.rtype, "normal"))
        _upsert_metrics(db, rows)
        total += len(rows)
    if total:
        print(f"Backfilled {total} samples ({hours:g} h of history; {ML_HISTORY_HOURS // 24} days for demo-ml-api).")


def sync_resources(db, ids: dict, want: dict, applied: dict, now: datetime, keepalive: bool) -> dict:
    """Write each resource's state/tags when its scenarios changed them. With
    keepalive, also refresh every row: real discovery marks provider ids it
    cannot find as deleted, and the demo resources must stay alive."""
    for key, rid in ids.items():
        target = (want[key]["state"], resource_tags(key, want[key]["untag"]))
        if applied.get(key) != target:
            db.table("resources").update({"state": target[0], "tags": target[1], "last_seen": now.isoformat()}) \
                .eq("id", rid).execute()
            applied[key] = target
    if keepalive:
        by_state: dict = {}
        for key, rid in ids.items():
            by_state.setdefault(want[key]["state"], []).append(rid)
        for state, rids in by_state.items():
            db.table("resources").update({"state": state, "last_seen": now.isoformat()}).in_("id", rids).execute()
    return applied


def bucket_time(now: datetime, seconds: int) -> datetime:
    """Start of the slot `now` falls in; samples in one slot overwrite each other."""
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def live_rows(ids: dict, want: dict, gen: Generator, now: datetime) -> list:
    rows = []
    for key, rid in ids.items():
        r = RESOURCES[key]
        if r.rtype in PROFILES and not want[key]["silent"]:
            rows += _rows(rid, now, gen.sample(key, r.rtype, want[key]["mode"]))
    return rows


def _stdin_reader(q: "queue.Queue[str]") -> None:
    for line in sys.stdin:
        q.put(line.strip())


def _story_step(index: int) -> None:
    minute, changes, note = STORY[index]
    for action, name in changes:
        change(action, [name])
    print(f"\n=== story {index + 1}/{len(STORY)} (T+{minute} min) ===\n{note}\n")


def start(db, interval: float, history_hours: float, story: bool = False,
          bucket_seconds: int = DEFAULT_BUCKET_SECONDS) -> None:
    if story:
        write_active(set())
    active = read_active()
    want = desired(active)
    ids = seed(db, history_hours, want)
    gen = Generator()
    backfill(db, ids, gen, history_hours)

    print("Scenarios:")
    print(format_list(active))
    print(f"\nWriting live samples every {interval:g}s into {bucket_seconds}s slots. Type `on|off|toggle <scenario ...|all>`, "
          "`list` or `quit`.\n")

    commands: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(commands,), daemon=True).start()

    per_minute = max(1, int(60 / interval))
    applied: dict = {}
    last_active = active
    tick = 0
    started = time.monotonic()
    next_step = 0 if story else len(STORY)
    try:
        while True:
            while not commands.empty():
                parts = commands.get().split()
                if not parts:
                    continue
                if parts[0] in ("quit", "exit", "q"):
                    return
                if parts[0] in ("list", "status", "ls"):
                    print(format_list(read_active()))
                else:
                    print(change(parts[0], parts[1:]))
            while next_step < len(STORY) and time.monotonic() - started >= STORY[next_step][0] * 60:
                _story_step(next_step)
                next_step += 1
                if next_step == len(STORY):
                    print("Story finished; still streaming. `quit` or Ctrl+C to stop.")

            active = read_active()
            if active != last_active:
                print("on: " + (", ".join(sorted(active)) or "nothing (all normal)"))
            last_active = active
            want = desired(active)

            now = datetime.now(timezone.utc)
            try:
                applied = sync_resources(db, ids, want, applied, now, keepalive=tick % per_minute == 0)
                rows = live_rows(ids, want, gen, bucket_time(now, bucket_seconds))
                if rows:
                    _upsert_metrics(db, rows)
            except Exception as e:  # network blip: keep the demo running
                print(f"write failed: {e}")
            tick += 1
            if tick % per_minute == 0:
                print(f"{now:%H:%M:%S} live ({tick} ticks); on: {', '.join(sorted(active)) or 'nothing'}")
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopped. Clean up with: python scripts/telemetry_demo.py cleanup")


def train_ml(db) -> None:
    """Train the EC2 Isolation Forest now from demo-ml-api's history, as the
    nightly retrain would. It writes the backend's model directory
    (ML_MODEL_PATH, default <repo>/models), so it only takes effect when the
    backend runs from this checkout; the backend reloads the file by itself."""
    sys.path.insert(0, str(ROOT))
    import pandas as pd

    from ml.features import build_training_dataset
    from ml.trainer import ModelTrainer

    rid = _ids_by_key(db).get("ml")
    if not rid:
        sys.exit("demo-ml-api not found: run `start` once first (it backfills its history).")
    records, page = [], 1000
    while True:
        batch = (db.table("resource_metrics").select("time, metric_name, value").eq("resource_id", rid)
                 .order("time").range(len(records), len(records) + page - 1).execute().data or [])
        records += batch
        if len(batch) < page:
            break
    df = pd.DataFrame(records)
    if df.empty:
        sys.exit("demo-ml-api has no samples yet: run `start` first.")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    features = build_training_dataset(df, "ec2")
    print(f"Training the ec2 model on {len(features)} samples from demo-ml-api. This replaces "
          "if_ec2_latest.pkl for every EC2 instance in this database until the nightly retrain.")
    trainer = ModelTrainer()
    trainer.train_isolation_forest(features, "ec2", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    result = trainer.last_result
    if result.get("promoted"):
        print(f"Promoted {result['version']} to {trainer.models_dir} "
              f"(holdout flag rate {result['holdout_flag_rate']:.1%}).")
    else:
        print(f"Not promoted: {result.get('reason')} (holdout flag rate {result.get('holdout_flag_rate')}).")


def cleanup(db) -> None:
    ids = [r["id"] for r in _demo_rows(db)]
    if not ids:
        print("No demo resources found.")
    else:
        for table in ("anomalies", "optimization_actions", "cost_records"):
            try:
                db.table(table).delete().in_("resource_id", ids).execute()
            except Exception as e:
                print(f"could not clear {table}: {e}")
        try:
            db.table("resources").delete().in_("id", ids).execute()  # metrics cascade
            print(f"Removed {len(ids)} demo resources and their metrics.")
        except Exception:
            # Append-only audit rows still reference them: keep the rows, drop the data.
            db.table("resource_metrics").delete().in_("resource_id", ids).execute()
            db.table("resources").update({"state": "deleted"}).in_("id", ids).execute()
            print(f"Removed demo metrics; {len(ids)} resources marked deleted (audit rows reference them).")
            ids = []
        if ids:
            try:
                db.table("cloud_accounts").delete().eq("account_id", DEMO_ACCOUNT).execute()
            except Exception:
                pass
    STATE_FILE.unlink(missing_ok=True)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser("start", help="seed demo resources and stream live samples")
    p_start.add_argument("--interval", type=float, default=10.0, help="seconds between samples (default 10)")
    p_start.add_argument("--history-hours", type=float, default=6.0, help="history to backfill (default 6)")
    p_start.add_argument("--bucket-seconds", type=int, default=DEFAULT_BUCKET_SECONDS,
                         help=f"one stored sample per slot of this many seconds (default {DEFAULT_BUCKET_SECONDS})")
    p_start.add_argument("--story", action="store_true", help="play the narrated timeline through every scenario")
    for action in ("on", "off", "toggle"):
        p = sub.add_parser(action, help=f"switch scenarios {action} (works while `start` runs)")
        p.add_argument("scenarios", nargs="+", help=f"all | {' | '.join(SCENARIOS)}")
    sub.add_parser("list", aliases=["status"], help="show scenarios, what each shows, and which are on")
    sub.add_parser("train-ml", help="train the EC2 Isolation Forest from demo-ml-api's history (local backend)")
    sub.add_parser("cleanup", help="remove demo resources and samples")
    args = parser.parse_args()

    if args.cmd in ("on", "off", "toggle"):
        print(change(args.cmd, args.scenarios))
    elif args.cmd in ("list", "status"):
        print(format_list(read_active()))
    elif args.cmd == "start":
        if args.interval < 1:
            sys.exit("--interval must be at least 1 second")
        if args.bucket_seconds < args.interval:
            sys.exit("--bucket-seconds must be at least --interval")
        start(_client(), args.interval, args.history_hours, story=args.story, bucket_seconds=args.bucket_seconds)
    elif args.cmd == "train-ml":
        train_ml(_client())
    elif args.cmd == "cleanup":
        cleanup(_client())


if __name__ == "__main__":
    main()
