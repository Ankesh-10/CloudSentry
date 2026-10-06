import glob
import json
import logging
import os
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest

from backend.app.config import settings
from ml.inference import DEFAULT_MODELS_DIR, metadata_path, read_metadata

logger = logging.getLogger(__name__)

MIN_TRAINING_ROWS = 500
HOLDOUT_FRACTION = 0.2


def _feature_stats(df: pd.DataFrame) -> tuple[dict, dict]:
    numeric = df.select_dtypes(include=[np.number])
    mean = {c: float(v) for c, v in numeric.mean().items() if np.isfinite(v)}
    std = {c: float(v) for c, v in numeric.std(ddof=0).items() if np.isfinite(v)}
    return mean, std


def feature_drift(df: pd.DataFrame, previous: dict | None) -> float | None:
    """Mean absolute shift of feature means, in units of the previous model's
    training std. ~0 = same distribution; > 1 = the data moved a lot."""
    if not previous or not previous.get("feature_mean"):
        return None
    mean, _ = _feature_stats(df)
    shifts = []
    for col, prev_mean in previous["feature_mean"].items():
        prev_std = (previous.get("feature_std") or {}).get(col) or 0.0
        if col in mean and prev_std > 1e-9:
            shifts.append(abs(mean[col] - prev_mean) / prev_std)
    return round(float(np.mean(shifts)), 4) if shifts else None


class ModelTrainer:
    def __init__(self, models_dir: str | None = None):
        self.models_dir = models_dir or settings.ML_MODEL_PATH or DEFAULT_MODELS_DIR
        os.makedirs(self.models_dir, exist_ok=True)
        self.last_result: dict = {}

    @staticmethod
    def _atomic_dump(model, path: str) -> None:
        # Inference may load the file concurrently: never expose a partial write.
        tmp = f"{path}.tmp"
        joblib.dump(model, tmp)
        os.replace(tmp, path)

    @staticmethod
    def _atomic_json(payload: dict, path: str) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, path)

    def train_isolation_forest(self, features_df: pd.DataFrame, resource_type: str, version_date: str | None = None):
        """Train, validate on a time-ordered holdout, and promote to `latest`
        only if validation passes. Returns True when a model was promoted;
        details (including a rejection reason) are in self.last_result."""
        self.last_result = {"resource_type": resource_type, "promoted": False}
        if len(features_df) < MIN_TRAINING_ROWS:
            logger.warning("Not enough data to train IF model for %s. Need %s, got %s",
                           resource_type, MIN_TRAINING_ROWS, len(features_df))
            self.last_result["reason"] = "insufficient_data"
            return False

        # Features are time-ordered: train on the past, validate on the most
        # recent slice, which is what the model will score next.
        split = int(len(features_df) * (1 - HOLDOUT_FRACTION))
        train, holdout = features_df.iloc[:split], features_df.iloc[split:]

        logger.info("Training Isolation Forest for %s with %s samples (%s holdout)...",
                    resource_type, len(train), len(holdout))
        model = IsolationForest(
            n_estimators=100,
            contamination=settings.ML_CONTAMINATION,
            random_state=42,
            n_jobs=settings.ML_N_JOBS,
        )
        model.fit(train)

        # Gate: the holdout is (mostly) the normal behaviour the model will
        # score next. Flagging far more than the expected contamination share
        # means the model would raise a flood of false anomalies.
        flag_rate = float((model.decision_function(holdout) < settings.ML_IF_DECISION_THRESHOLD).mean())
        previous = read_metadata(self.models_dir, resource_type)
        drift = feature_drift(features_df, previous)
        self.last_result.update(holdout_flag_rate=round(flag_rate, 4), drift_vs_previous=drift)
        if flag_rate > settings.ML_MAX_HOLDOUT_FLAG_RATE:
            logger.error("Rejected %s model: flags %.1f%% of holdout (max %.1f%%); keeping previous model",
                         resource_type, flag_rate * 100, settings.ML_MAX_HOLDOUT_FLAG_RATE * 100)
            self.last_result["reason"] = "validation_failed"
            return False
        if drift is not None and drift > settings.ML_DRIFT_WARN:
            logger.warning("Feature drift for %s: %.2f std from the previous model's training data", resource_type, drift)

        now = datetime.now(timezone.utc)
        mean, std = _feature_stats(train)
        version = f"if_{resource_type}_{now.strftime('%Y%m%dT%H%M%SZ')}"
        metadata = {
            "version": version,
            "resource_type": resource_type,
            "trained_at": now.isoformat(),
            "n_samples": int(len(train)),
            "n_holdout": int(len(holdout)),
            "holdout_flag_rate": round(flag_rate, 4),
            "drift_vs_previous": drift,
            "contamination": settings.ML_CONTAMINATION,
            "decision_threshold": settings.ML_IF_DECISION_THRESHOLD,
            "sklearn_version": sklearn.__version__,
            "feature_columns": [str(c) for c in train.columns],
            "feature_mean": mean,
            "feature_std": std,
        }

        latest_path = os.path.join(self.models_dir, f"if_{resource_type}_latest.pkl")
        if version_date:
            dated = os.path.join(self.models_dir, f"if_{resource_type}_{version_date}.pkl")
            self._atomic_dump(model, dated)
            self._retain_versions(resource_type, keep=5)
        # Metadata first: inference reloads on the .pkl mtime, so by then the
        # sidecar already describes the model it is about to load.
        self._atomic_json(metadata, metadata_path(self.models_dir, resource_type))
        self._atomic_dump(model, latest_path)
        logger.info("Promoted %s (holdout flag rate %.1f%%)", version, flag_rate * 100)
        self.last_result.update(promoted=True, version=version)
        return True

    def _retain_versions(self, resource_type: str, keep: int = 5):
        pattern = os.path.join(self.models_dir, f"if_{resource_type}_20*.pkl")
        files = sorted(glob.glob(pattern))
        extra = files[:-keep] if len(files) > keep else []
        for path in extra:
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("Could not delete old model %s: %s", path, e)
