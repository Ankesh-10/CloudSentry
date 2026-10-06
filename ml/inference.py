import glob
import json
import logging
import os

import joblib
import pandas as pd

from backend.app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../models"))


def metadata_path(models_dir: str, resource_type: str) -> str:
    return os.path.join(models_dir, f"if_{resource_type}_latest.json")


def read_metadata(models_dir: str, resource_type: str) -> dict | None:
    try:
        with open(metadata_path(models_dir, resource_type), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _sklearn_version() -> str | None:
    try:
        import sklearn
        return sklearn.__version__
    except Exception:
        return None


class InferenceEngine:
    def __init__(self, models_dir: str | None = None):
        self.models_dir = models_dir or settings.ML_MODEL_PATH or DEFAULT_MODELS_DIR
        # resource_type -> (mtime, model, metadata); reloaded when the file on
        # disk changes so a nightly retrain takes effect without a restart.
        self.models: dict = {}

    def available_models(self) -> list[str]:
        return sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.models_dir, "if_*_latest.pkl")))

    def model_info(self) -> dict[str, dict]:
        """Metadata of each promoted model (for /system/health)."""
        info = {}
        for name in self.available_models():
            rtype = name[len("if_"):-len("_latest.pkl")]
            meta = read_metadata(self.models_dir, rtype) or {}
            info[rtype] = {k: meta.get(k) for k in ("version", "trained_at", "n_samples", "holdout_flag_rate",
                                                    "drift_vs_previous", "sklearn_version")}
        return info

    def _load_model(self, resource_type: str):
        model_path = os.path.join(self.models_dir, f"if_{resource_type}_latest.pkl")
        try:
            mtime = os.path.getmtime(model_path)
        except OSError:
            self.models.pop(resource_type, None)
            return None
        cached = self.models.get(resource_type)
        if cached and cached[0] == mtime:
            return cached[1]
        meta = read_metadata(self.models_dir, resource_type) or {}
        trained_with = meta.get("sklearn_version")
        if trained_with and trained_with != _sklearn_version():
            # A pickle from another scikit-learn version may load but score
            # differently (or not load). Fall back to the statistical detectors
            # until the next retrain writes a compatible model.
            logger.error("Model %s was trained with scikit-learn %s but %s is installed; not loading",
                         model_path, trained_with, _sklearn_version())
            self.models.pop(resource_type, None)
            return None
        try:
            # joblib.load unpickles: models_dir must be writable only by this app.
            model = joblib.load(model_path)
        except Exception as e:
            logger.error("Failed to load model %s: %s", model_path, e)
            return cached[1] if cached else None
        self.models[resource_type] = (mtime, model, meta)
        return model

    def _version(self, resource_type: str) -> str:
        cached = self.models.get(resource_type)
        meta = cached[2] if cached and len(cached) > 2 else {}
        return meta.get("version") or f"if_{resource_type}_latest"

    def predict(self, features_df: pd.DataFrame, resource_type: str) -> dict:
        model = self._load_model(resource_type)
        if model is None:
            return {"is_anomaly": False, "reason": "Model not trained"}
        if features_df.empty:
            return {"is_anomaly": False, "reason": "Empty features"}

        # decision_function = score_samples - offset_, where offset_ is set by
        # `contamination` at fit time: < 0 means "more anomalous than the
        # expected contamination share". Raw score_samples sit around -0.4..-0.6
        # for *normal* points, so thresholding them at -0.3 flagged everything.
        decision = float(model.decision_function(features_df)[-1])
        threshold = settings.ML_IF_DECISION_THRESHOLD
        is_anomaly = decision < threshold
        margin = threshold - decision
        confidence = min(0.99, 0.5 + margin * 2) if margin > 0 else 0.0
        severity = "HIGH" if decision < threshold - 0.15 else "MEDIUM" if decision < threshold - 0.05 else "LOW"
        return {
            "is_anomaly": is_anomaly,
            "score": decision,
            "confidence": float(confidence),
            "reason": f"Isolation Forest decision {decision:.3f} < threshold {threshold}" if is_anomaly else "",
            "severity": severity,
            "model_version": self._version(resource_type),
        }
