import os
import time

import numpy as np
import pytest

from ml.evaluation import evaluate_scenario
from ml.features import build_training_dataset
from ml.inference import InferenceEngine
from ml.synthetic.generator import generate_from_scenario
from ml.trainer import ModelTrainer

SCENARIOS = os.path.join(os.path.dirname(__file__), "..", "ml", "synthetic", "scenarios")


def _features(scenario, rtype):
    np.random.seed(0)
    df, labels = generate_from_scenario(os.path.join(SCENARIOS, scenario))
    feats = build_training_dataset(df, rtype)
    return feats, labels.loc[feats.index]


def test_inference_does_not_flag_normal_behaviour(tmp_path):
    """Regression: thresholding score_samples at -0.3 flagged ~100% of points."""
    feats, labels = _features("idle_ec2.json", "ec2")
    normal = feats[~labels.values]
    split = len(normal) // 2
    assert ModelTrainer(models_dir=str(tmp_path)).train_isolation_forest(normal.iloc[:split], "ec2")
    engine = InferenceEngine(models_dir=str(tmp_path))
    flagged = sum(engine.predict(normal.iloc[i:i + 1], "ec2")["is_anomaly"] for i in range(split, len(normal), 10))
    checked = len(range(split, len(normal), 10))
    assert flagged / checked < 0.2


def test_inference_flags_anomalous_behaviour(tmp_path):
    feats, labels = _features("runaway_lambda.json", "lambda")
    normal = feats[~labels.values]
    ModelTrainer(models_dir=str(tmp_path)).train_isolation_forest(normal, "lambda")
    engine = InferenceEngine(models_dir=str(tmp_path))
    anomalous = feats[labels.values]
    hits = sum(engine.predict(anomalous.iloc[i:i + 1], "lambda")["is_anomaly"] for i in range(len(anomalous)))
    assert hits / len(anomalous) > 0.8


def test_model_is_reloaded_after_retrain(tmp_path):
    feats, _ = _features("idle_ec2.json", "ec2")
    trainer = ModelTrainer(models_dir=str(tmp_path))
    trainer.train_isolation_forest(feats.iloc[:600], "ec2")
    engine = InferenceEngine(models_dir=str(tmp_path))
    first = engine._load_model("ec2")
    time.sleep(0.05)
    trainer.train_isolation_forest(feats.iloc[600:1200], "ec2")
    os.utime(os.path.join(tmp_path, "if_ec2_latest.pkl"))
    assert engine._load_model("ec2") is not first


def test_versioned_models_retained(tmp_path):
    feats, _ = _features("idle_ec2.json", "ec2")
    trainer = ModelTrainer(models_dir=str(tmp_path))
    for day in range(1, 8):
        trainer.train_isolation_forest(feats.iloc[:600], "ec2", version_date=f"2026-01-0{day}")
    dated = sorted(f for f in os.listdir(tmp_path) if f.startswith("if_ec2_20"))
    assert len(dated) == 5 and dated[-1] == "if_ec2_2026-01-07.pkl"
    assert "if_ec2_latest.pkl" in os.listdir(tmp_path)


@pytest.mark.parametrize("scenario", ["runaway_lambda.json"])
def test_offline_evaluation_on_holdout_meets_targets(scenario):
    np.random.seed(0)
    metrics = evaluate_scenario(scenario)
    assert metrics["recall"] >= 0.70
    assert metrics["precision"] >= 0.60


def test_evaluation_does_not_touch_production_models():
    prod = InferenceEngine().models_dir
    before = {f: os.path.getmtime(os.path.join(prod, f)) for f in os.listdir(prod)} if os.path.isdir(prod) else {}
    np.random.seed(0)
    evaluate_scenario("idle_ec2.json")
    after = {f: os.path.getmtime(os.path.join(prod, f)) for f in os.listdir(prod)} if os.path.isdir(prod) else {}
    assert before == after
