import json
import logging
import os
import tempfile
from sklearn.metrics import classification_report, precision_score, recall_score, f1_score
from ml.synthetic.generator import generate_from_scenario
from ml.features import build_training_dataset
from ml.trainer import ModelTrainer
from ml.inference import InferenceEngine
from backend.app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def evaluate_scenario(scenario_file: str):
    logger.info(f"--- Evaluating Scenario: {scenario_file} ---")
    scenario_path = os.path.join(os.path.dirname(__file__), "synthetic", "scenarios", scenario_file)
    
    with open(scenario_path, 'r') as f:
        config = json.load(f)
    resource_type = config.get("resource_type", "ec2")
    
    # 1. Generate data
    df_raw, labels = generate_from_scenario(scenario_path)
    logger.info(f"Generated {len(df_raw)} raw metric points")
    
    # 2. Build features
    features_df = build_training_dataset(df_raw, resource_type)
    logger.info(f"Built {len(features_df)} feature vectors")
    
    # Align labels with features (first 288 points were dropped by feature builder)
    labels = labels.loc[features_df.index]
    
    # 3. Train on the first half, score only the held-out second half, in a
    # temporary directory so evaluation never overwrites production models.
    train_size = len(features_df) // 2
    train_features = features_df.iloc[:train_size]
    holdout = features_df.iloc[train_size:]
    labels = labels.iloc[train_size:]

    with tempfile.TemporaryDirectory() as tmp:
        trainer = ModelTrainer(models_dir=tmp)
        if not trainer.train_isolation_forest(train_features, resource_type):
            logger.error("Failed to train model.")
            return None
        model = InferenceEngine(models_dir=tmp)._load_model(resource_type)
        # Same decision rule as production InferenceEngine.predict().
        decisions = model.decision_function(holdout)
    predictions = [d < settings.ML_IF_DECISION_THRESHOLD for d in decisions]

    # 5. Evaluate
    precision = precision_score(labels, predictions, zero_division=0)
    recall = recall_score(labels, predictions, zero_division=0)
    f1 = f1_score(labels, predictions, zero_division=0)
    
    logger.info(f"Results for {scenario_file}:")
    logger.info(f"Precision: {precision:.2f} (Target: >= 0.75)")
    logger.info(f"Recall:    {recall:.2f} (Target: >= 0.70)")
    logger.info(f"F1 Score:  {f1:.2f} (Target: >= 0.72)")
    print(classification_report(labels, predictions, target_names=["Normal", "Anomaly"], zero_division=0))
    return {"precision": precision, "recall": recall, "f1": f1}

def main():
    scenarios = ["idle_ec2.json", "runaway_lambda.json"]
    for s in scenarios:
        evaluate_scenario(s)

if __name__ == "__main__":
    main()
