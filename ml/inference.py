import os
import joblib
import pandas as pd
import logging
from backend.app.config import settings

logger = logging.getLogger(__name__)

class InferenceEngine:
    def __init__(self):
        self.models_dir = os.path.join(os.path.dirname(__file__), "../models")
        self.models = {}
        
    def _load_model(self, resource_type: str):
        if resource_type in self.models:
            return self.models[resource_type]
            
        model_path = os.path.join(self.models_dir, f"if_{resource_type}_latest.pkl")
        if not os.path.exists(model_path):
            return None
            
        try:
            model = joblib.load(model_path)
            self.models[resource_type] = model
            return model
        except Exception as e:
            logger.error(f"Failed to load model {model_path}: {e}")
            return None
            
    def predict(self, features_df: pd.DataFrame, resource_type: str) -> dict:
        """
        Scores the feature vector using the Isolation Forest model.
        Returns anomaly info.
        """
        model = self._load_model(resource_type)
        if model is None:
            return {"is_anomaly": False, "reason": "Model not trained"}
            
        if features_df.empty:
            return {"is_anomaly": False, "reason": "Empty features"}
            
        # score_samples returns negative anomaly scores. Lower means more anomalous.
        scores = model.score_samples(features_df)
        score = scores[-1]
        
        # Determine if it's an anomaly based on threshold from config
        is_anomaly = score < settings.ML_ANOMALY_THRESHOLD
        
        # Calculate a basic confidence metric based on how far below threshold it is
        # e.g., if threshold is -0.3, and score is -0.5, it's very anomalous
        diff = settings.ML_ANOMALY_THRESHOLD - score
        confidence = min(0.99, 0.5 + (diff * 2) if diff > 0 else 0.0)
        
        reason = ""
        if is_anomaly:
            reason = f"ML model detected unusual behavior (Score: {score:.2f}, Threshold: {settings.ML_ANOMALY_THRESHOLD})"
            
        return {
            "is_anomaly": is_anomaly,
            "score": float(score),
            "confidence": float(confidence),
            "reason": reason
        }
