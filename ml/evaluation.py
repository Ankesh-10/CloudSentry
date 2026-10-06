"""Offline evaluation of the Isolation Forest on labelled synthetic scenarios.

    python -m ml.evaluation            # report
    python -m ml.evaluation --strict   # exit 1 if any target is missed (CI)

Deterministic: fixed generator start time and seed, so results only change when
the code or the scenarios do. Never writes to the production models directory.
"""
import argparse
import json
import logging
import os
import sys
import tempfile

from sklearn.metrics import classification_report, f1_score, precision_score, recall_score

from backend.app.config import settings
from ml.features import build_training_dataset
from ml.inference import InferenceEngine
from ml.synthetic.generator import generate_from_scenario
from ml.trainer import ModelTrainer

logger = logging.getLogger(__name__)

TARGETS = {"precision": 0.60, "recall": 0.70}
SCENARIOS = ["runaway_lambda.json"]
SEED = 0


def evaluate_scenario(scenario_file: str, seed: int | None = SEED):
    logger.info("--- Evaluating Scenario: %s ---", scenario_file)
    scenario_path = os.path.join(os.path.dirname(__file__), "synthetic", "scenarios", scenario_file)

    with open(scenario_path, 'r') as f:
        config = json.load(f)
    resource_type = config.get("resource_type", "ec2")

    df_raw, labels = generate_from_scenario(scenario_path, seed=seed)
    features_df = build_training_dataset(df_raw, resource_type)
    # Align labels with features (the feature builder drops a burn-in window).
    labels = labels.loc[features_df.index]

    # Train on the first half, score only the held-out second half, in a
    # temporary directory so evaluation never overwrites production models.
    train_size = len(features_df) // 2
    train_features = features_df.iloc[:train_size]
    holdout = features_df.iloc[train_size:]
    labels = labels.iloc[train_size:]

    with tempfile.TemporaryDirectory() as tmp:
        trainer = ModelTrainer(models_dir=tmp)
        if not trainer.train_isolation_forest(train_features, resource_type):
            logger.error("Failed to train model: %s", trainer.last_result)
            return None
        model = InferenceEngine(models_dir=tmp)._load_model(resource_type)
        # Same decision rule as production InferenceEngine.predict().
        decisions = model.decision_function(holdout)
    predictions = [d < settings.ML_IF_DECISION_THRESHOLD for d in decisions]

    metrics = {
        "precision": precision_score(labels, predictions, zero_division=0),
        "recall": recall_score(labels, predictions, zero_division=0),
        "f1": f1_score(labels, predictions, zero_division=0),
    }
    logger.info("Results for %s: %s (targets %s)", scenario_file, metrics, TARGETS)
    logger.debug("\n%s", classification_report(labels, predictions, target_names=["Normal", "Anomaly"],
                                               zero_division=0))
    return metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="exit non-zero if a target is missed")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    failed = []
    for scenario in SCENARIOS:
        result = evaluate_scenario(scenario)
        if result is None:
            failed.append(f"{scenario}: training failed")
            continue
        for metric, target in TARGETS.items():
            if result[metric] < target:
                failed.append(f"{scenario}: {metric} {result[metric]:.2f} < {target}")
    for line in failed:
        print(f"MISSED {line}")
    if not failed:
        print("All evaluation targets met.")
    return 1 if (failed and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
