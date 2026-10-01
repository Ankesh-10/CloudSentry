import glob
import logging
import os

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest

from backend.app.config import settings
from ml.inference import DEFAULT_MODELS_DIR

logger = logging.getLogger(__name__)


class ModelTrainer:
    def __init__(self, models_dir: str | None = None):
        self.models_dir = models_dir or settings.ML_MODEL_PATH or DEFAULT_MODELS_DIR
        os.makedirs(self.models_dir, exist_ok=True)

    @staticmethod
    def _atomic_dump(model, path: str) -> None:
        # Inference may load the file concurrently: never expose a partial write.
        tmp = f"{path}.tmp"
        joblib.dump(model, tmp)
        os.replace(tmp, path)

    def train_isolation_forest(self, features_df: pd.DataFrame, resource_type: str, version_date: str | None = None):
        if len(features_df) < 500:
            logger.warning("Not enough data to train IF model for %s. Need 500, got %s", resource_type, len(features_df))
            return False

        logger.info("Training Isolation Forest for %s with %s samples...", resource_type, len(features_df))
        model = IsolationForest(
            n_estimators=100,
            contamination=settings.ML_CONTAMINATION,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(features_df)

        latest_path = os.path.join(self.models_dir, f"if_{resource_type}_latest.pkl")
        if version_date:
            dated = os.path.join(self.models_dir, f"if_{resource_type}_{version_date}.pkl")
            self._atomic_dump(model, dated)
            self._retain_versions(resource_type, keep=5)
        self._atomic_dump(model, latest_path)
        logger.info("Saved trained model to %s", latest_path)
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
