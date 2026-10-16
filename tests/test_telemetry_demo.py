"""scripts/telemetry_demo.py: every scenario produces what `list` promises.

The demo's own data goes through the real anomaly detector and policy engine
(fake database, no cloud), with the pipeline run once a simulated minute, the
way the backend runs it with JOB_PIPELINE_MINUTES=1.
"""
import importlib.util
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.app.config import settings

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc).replace(microsecond=0)
SLOT = 30  # --bucket-seconds default
WRITES_PER_SLOT = 3  # --interval 10 into 30 s slots: the last write of a slot is what is stored

# Mirrors the seeded policies (migrations 004, 006, 010 and 013); test_policies_match_migrations
# keeps the conditions honest.
POLICIES = [
    {"name": "Auto-stop idle EC2 instances", "resource_type": "ec2", "anomaly_type": "idle_compute",
     "conditions": {"AND": [{"field": "anomaly.idle_score", "op": "gte", "value": 0.80},
                            {"field": "anomaly.confidence", "op": "gte", "value": 0.75},
                            {"field": "resource.protected", "op": "eq", "value": False},
                            {"field": "resource.age_minutes", "op": "gte", "value": 30}]},
     "action_type": "stop_ec2", "risk_level": "MEDIUM", "requires_approval": False, "priority": 100},
    {"name": "Limit runaway Lambda concurrency", "resource_type": "lambda", "anomaly_type": "runaway_lambda",
     "conditions": {"AND": [{"field": "anomaly.confidence", "op": "gte", "value": 0.90},
                            {"field": "resource.protected", "op": "eq", "value": False}]},
     "action_type": "limit_lambda", "risk_level": "MEDIUM", "requires_approval": False, "priority": 100},
    {"name": "Recommend unused EBS volumes (never auto-delete)", "resource_type": "ebs",
     "anomaly_type": "unused_volume",
     "conditions": {"AND": [{"field": "resource.state", "op": "eq", "value": "available"}]},
     "action_type": "recommend_review", "risk_level": "LOW", "requires_approval": True, "priority": 100},
    {"name": "Apply tags to untagged resources", "resource_type": "*", "anomaly_type": "untagged_resource",
     "conditions": {"AND": [{"field": "resource.protected", "op": "eq", "value": False}]},
     "action_type": "apply_tags", "risk_level": "LOW", "requires_approval": False, "priority": 200},
    {"name": "Review idle RDS databases", "resource_type": "rds", "anomaly_type": "idle_database",
     "conditions": {"AND": [{"field": "resource.state", "op": "eq", "value": "available"}]},
     "action_type": "recommend_review", "risk_level": "LOW", "requires_approval": True, "priority": 100},
    {"name": "Review RDS databases running out of storage", "resource_type": "rds", "anomaly_type": "low_storage",
     "conditions": {"AND": [{"field": "resource.state", "op": "eq", "value": "available"}]},
     "action_type": "recommend_review", "risk_level": "LOW", "requires_approval": True, "priority": 100},
]

# scenario -> (anomalies it must raise, anomalies it may also raise, action it must lead to or None).
# Exact sets: going idle must not also read as a CPU "spike", and a runaway
# Lambda must stay runaway_lambda while it lasts.
IDLE = ({"idle_compute"}, set())
EXPECTED = {
    "cpu-spike": ({"unusual_cpu_spike"}, set(), None),
    "idle-ec2": (*IDLE, "stop_ec2"),
    "idle-protected": (*IDLE, None),
    "idle-autoscaled": (*IDLE, None),
    "idle-do-not-stop": (*IDLE, None),
    "untagged": ({"untagged_resource"}, set(), "apply_tags"),
    "unused-volume": ({"unused_volume"}, set(), "recommend_review"),
    "lambda-runaway": ({"runaway_lambda"}, set(), "limit_lambda"),
    "lambda-errors": ({"runaway_lambda"}, set(), None),
    "lambda-surge": ({"statistical_anomaly"}, set(), None),
    "rds-busy": ({"statistical_anomaly"}, set(), None),
    "rds-idle": ({"idle_database"}, set(), "recommend_review"),
    "rds-storage": ({"low_storage"}, set(), "recommend_review"),
}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("telemetry_demo", ROOT / "scripts" / "telemetry_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setenv("REQUIRED_TAGS", settings.REQUIRED_TAGS)
    return module


@pytest.fixture
def backend(fake_db, tmp_path, monkeypatch):
    """Quick-demo backend settings, and no model unless a test trains one."""
    monkeypatch.setattr(settings, "ML_IDLE_WINDOW_HOURS", 0.1)
    monkeypatch.setattr(settings, "ML_IF_DECISION_THRESHOLD", -0.05)  # the default (conftest pins 0)
    monkeypatch.setattr(settings, "ML_MODEL_PATH", str(tmp_path / "models"))
    (tmp_path / "models").mkdir()
    for i, p in enumerate(POLICIES):
        fake_db.rows("policies").append({**p, "id": f"p{i}", "enabled": True, "conditions": json.dumps(p["conditions"])})
    return fake_db


def _history(demo, gen, key, end):
    r = demo.RESOURCES[key]
    out = []
    for i in range(int((r.history_hours or 6) * 12), 0, -1):
        t = end - timedelta(minutes=5 * i)
        out += [{"time": t, "metric_name": m, "value": v} for m, v in gen.sample(key, r.rtype, "normal").items()]
    return out


def _slot(demo, gen, key, mode, t):
    rtype = demo.RESOURCES[key].rtype
    for _ in range(WRITES_PER_SLOT):
        values = gen.sample(key, rtype, mode)
    return [{"time": t, "metric_name": m, "value": v} for m, v in values.items()]


def run_demo(demo, db, active, minutes=8, normal_minutes=2, seed=7):
    """Seed the demo resources, stream `normal_minutes` of normal samples, switch
    `active` on, then stream `minutes` more, running detection + policies once a
    minute. Returns {resource key: (anomaly types ever raised, action types)}."""
    from backend.app.services.anomaly_detector import AnomalyDetectorService
    from backend.app.services.policy_engine import PolicyEngine

    random.seed(seed)
    ids = demo.seed(db, 6.0, demo.desired(set()))
    gen = demo.Generator()
    start = NOW - timedelta(minutes=minutes + normal_minutes)
    records = {key: _history(demo, gen, key, start) if demo.RESOURCES[key].rtype in demo.PROFILES else []
               for key in ids}
    detector, engine = AnomalyDetectorService(), PolicyEngine()
    applied: dict = {}
    raised = {key: set() for key in ids}
    slots_per_minute = 60 // SLOT
    for minute in range(1, minutes + normal_minutes + 1):
        want = demo.desired(active if minute > normal_minutes else set())
        for s in range(slots_per_minute):
            t = start + timedelta(minutes=minute - 1, seconds=SLOT * (s + 1))
            for key in ids:
                if demo.RESOURCES[key].rtype in demo.PROFILES and not want[key]["silent"]:
                    records[key] += _slot(demo, gen, key, want[key]["mode"], t)
        now = start + timedelta(minutes=minute)
        applied = demo.sync_resources(db, ids, want, applied, now, keepalive=True)
        rows = {r["id"]: r for r in db.rows("resources")}
        for key, rid in ids.items():
            detector.process_resource(rows[rid], records[key], now)
        engine.evaluate_all()
        for a in db.rows("anomalies"):
            key = next(k for k, rid in ids.items() if rid == a["resource_id"])
            raised[key].add(a["anomaly_type"])
    actions = {key: {a["action_type"] for a in db.rows("optimization_actions") if a["resource_id"] == rid}
               for key, rid in ids.items()}
    return {key: (raised[key], actions[key]) for key in ids}


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_every_scenario_at_once_does_what_list_says(demo, backend, seed):
    on = [n for n, s in demo.SCENARIOS.items() if not s.masks and n != "ml-anomaly"]
    assert sorted(on) == sorted(EXPECTED)  # a new scenario needs an expectation here
    result = run_demo(demo, backend, set(on), seed=seed)
    for name in on:
        must, may, action = EXPECTED[name]
        raised, actions = result[demo.SCENARIOS[name].resource]
        assert must <= raised <= must | may, name
        assert actions == ({action} if action else set()), name


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_all_normal_raises_nothing(demo, backend, seed):
    result = run_demo(demo, backend, set(), minutes=20, seed=seed)
    assert {k: v for k, v in result.items() if v != (set(), set())} == {}


def test_actions_on_demo_resources_are_dry_run(demo, backend):
    run_demo(demo, backend, {"idle-ec2", "lambda-runaway", "untagged"})
    actions = backend.rows("optimization_actions")
    assert actions and all(a["dry_run"] is True for a in actions)
    assert all(r["metadata"]["synthetic"] is True for r in backend.rows("resources"))


def test_stopping_the_idle_worker_resolves_its_anomalies(demo, backend):
    def worker_anomalies():
        worker = next(r for r in backend.rows("resources") if r["name"] == "demo-batch-worker")
        return worker, {a["anomaly_type"]: a["status"] for a in backend.rows("anomalies")
                        if a["resource_id"] == worker["id"]}

    run_demo(demo, backend, {"idle-ec2"})
    assert worker_anomalies()[1]["idle_compute"] == "active"
    run_demo(demo, backend, {"idle-ec2", "stopped"}, minutes=2, normal_minutes=0)
    worker, anomalies = worker_anomalies()
    assert worker["state"] == "stopped"
    assert set(anomalies.values()) == {"resolved"}


def test_ml_anomaly_is_caught_by_the_trained_model(demo, backend):
    import pandas as pd

    from ml.features import build_training_dataset
    from ml.trainer import ModelTrainer

    random.seed(3)
    history = pd.DataFrame(_history(demo, demo.Generator(), "ml", NOW - timedelta(minutes=10)))
    history["time"] = pd.to_datetime(history["time"], utc=True)
    assert history["time"].nunique() >= settings.ML_MIN_POINTS_IF  # the IF path, not a fallback
    assert ModelTrainer().train_isolation_forest(build_training_dataset(history, "ec2"), "ec2")
    raised, actions = run_demo(demo, backend, {"ml-anomaly"})["ml"]
    assert "ml_behavioral_anomaly" in raised and actions == set()


def test_on_off_toggle_and_all(demo):
    assert demo.change("on", ["cpu-spike", "untagged"]) == "on: cpu-spike, untagged"
    demo.change("toggle", ["cpu-spike", "idle-ec2"])
    assert demo.read_active() == {"untagged", "idle-ec2"}
    demo.change("on", ["all"])
    assert demo.read_active() == {n for n, s in demo.SCENARIOS.items() if not s.masks}
    demo.change("off", ["all"])
    assert demo.read_active() == set()
    assert "unknown scenario" in demo.change("on", ["nope", "cpu-spike"])
    assert demo.read_active() == set()  # nothing applied on a typo
    demo.STATE_FILE.write_text("{not json")
    assert demo.read_active() == set()


def test_masking_scenarios_override_the_others(demo):
    want = demo.desired({"idle-ec2", "stopped"})
    assert want["worker"] == {"mode": "idle", "state": "stopped", "silent": True, "untag": False}
    assert all(w["silent"] for w in demo.desired({"outage", "cpu-spike"}).values())


def test_untagged_drops_only_the_required_tags(demo, monkeypatch):
    monkeypatch.setenv("REQUIRED_TAGS", "Project,Owner,CostCenter")
    healthy = demo.resource_tags("legacy")
    assert {"Project", "Owner", "CostCenter"} <= set(healthy)
    assert set(demo.resource_tags("legacy", untag=True)) == {"cloudsentry:demo"}
    assert demo.resource_tags("asg-node")["aws:autoscaling:groupName"] == "demo-web-asg"


def test_keepalive_revives_rows_discovery_marked_deleted(demo, fake_db):
    ids = demo.seed(fake_db, 6.0, demo.desired(set()))
    for r in fake_db.rows("resources"):
        r["state"] = "deleted"
    demo.sync_resources(fake_db, ids, demo.desired({"unused-volume"}), {}, NOW, keepalive=True)
    states = {r["name"]: r["state"] for r in fake_db.rows("resources")}
    assert states["demo-scratch-volume"] == "available" and states["demo-web-api"] == "running"


def test_samples_in_one_slot_overwrite_each_other(demo):
    t = datetime(2026, 1, 1, 12, 0, 29, tzinfo=timezone.utc)
    assert demo.bucket_time(t, 30) == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert demo.bucket_time(t + timedelta(seconds=1), 30) == datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)


def test_scenarios_and_story_are_consistent(demo):
    for name, sc in demo.SCENARIOS.items():
        assert sc.resource == "*" or sc.resource in demo.RESOURCES, name
        if sc.mode:
            assert sc.mode in demo.PROFILES[demo.RESOURCES[sc.resource].rtype], name
    switched_on = {n for _, changes, _ in demo.STORY for action, n in changes if action == "on"}
    assert switched_on == set(demo.SCENARIOS) - {"outage"}  # outage takes 30 min: not in the story
    assert [m for m, _, _ in demo.STORY] == sorted(m for m, _, _ in demo.STORY)


def test_demo_provider_ids_cannot_be_real(demo):
    for r in demo.RESOURCES.values():
        assert "demo" in r.provider_id
        if r.rtype == "ec2":
            assert not all(c in "0123456789abcdef" for c in r.provider_id.removeprefix("i-"))


def test_policies_match_migrations():
    import re

    sql = "".join((ROOT / "db" / "migrations" / f).read_text(encoding="utf-8")
                  for f in ("004_seed_policies.sql", "006_security_and_seed.sql", "010_recommend_review_policy.sql",
                            "013_rds_review_policies.sql"))
    seeded = [json.loads(c) for c in re.findall(r"'(\{\"AND\".*?\})'", sql)]
    for p in POLICIES:
        assert p["name"] in sql
        assert p["conditions"] in seeded, p["name"]
    assert "SET action_type = 'recommend_review'" in sql  # 010 remaps the EBS policy
