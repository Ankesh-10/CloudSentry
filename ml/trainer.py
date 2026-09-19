import os
import joblib
import pandas as pd
import logging
from sklearn.ensemble import IsolationForest
from backend.app.config import settings

logger = logging.getLogger(__name__)

class ModelTrainer:
    def __init__(self):
        self.models_dir = os.path.join(os.path.dirname(__file__), "../models")
        os.makedirs(self.models_dir, exist_ok=True)
        
    def train_isolation_forest(self, features_df: pd.DataFrame, resource_type: str):
        """
        Trains an Isolation Forest model and serializes it to disk.
        """
        if len(features_df) < 500:
            logger.warning(f"Not enough data to train IF model for {resource_type}. Need 500, got {len(features_df)}")
            return False
            
        logger.info(f"Training Isolation Forest for {resource_type} with {len(features_df)} samples...")
        
        # We assume features_df is already engineered by features.py and contains only numeric feature columns
        model = IsolationForest(
            n_estimators=100,
            contamination=0.001,
            random_state=42,
            n_jobs=-1
        )
        
        model.fit(features_df)
        
        model_path = os.path.join(self.models_dir, f"if_{resource_type}_latest.pkl")
        joblib.dump(model, model_path)
        logger.info(f"Saved trained model to {model_path}")
        return True
