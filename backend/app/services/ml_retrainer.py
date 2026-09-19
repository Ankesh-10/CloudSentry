import logging
import pandas as pd
from backend.app.db.supabase_client import get_supabase_client
from backend.app.db.asyncpg_pool import get_pool
from ml.trainer import ModelTrainer
from ml.features import build_training_dataset

logger = logging.getLogger(__name__)

class MLRetrainingService:
    def __init__(self):
        self.db = get_supabase_client()
        self.trainer = ModelTrainer()
        
    async def run(self):
        """
        Fetches the last 7 days of metrics for each active resource,
        engineers features, and retrains the Isolation Forest model.
        """
        logger.info("Starting ML Model Retraining cycle...")
        pool = get_pool()
        
        # 1. Get all active resources
        res = self.db.table("resources").select("id, resource_type").neq("state", "terminated").execute()
        resources = res.data
        if not resources:
            logger.info("No active resources found. Skipping ML retraining.")
            return
            
        # Group features by resource type so we train one generic model per resource type
        training_data_by_type = {}
            
        for r in resources:
            resource_id = r["id"]
            resource_type = r["resource_type"]
            
            # 2. Fetch last 7 days of metrics
            query = """
                SELECT time, metric_name, value 
                FROM resource_metrics 
                WHERE resource_id = $1 AND time > NOW() - INTERVAL '7 days'
                ORDER BY time ASC
            """
            async with pool.acquire() as conn:
                records = await conn.fetch(query, resource_id)
                
            if len(records) < 500:
                # Need sufficient data to even build proper rolling windows
                continue
                
            df = pd.DataFrame([dict(rec) for rec in records])
            
            # 3. Engineer features for the whole dataset
            features_df = build_training_dataset(df, resource_type)
            if features_df.empty or len(features_df) < 100:
                continue
                
            if resource_type not in training_data_by_type:
                training_data_by_type[resource_type] = []
                
            training_data_by_type[resource_type].append(features_df)
            
        # 4. Train models per resource type
        for resource_type, df_list in training_data_by_type.items():
            if not df_list:
                continue
                
            # Combine all instances of the same type into one large training set
            combined_features = pd.concat(df_list, ignore_index=True)
            
            if len(combined_features) >= 500:
                logger.info(f"Retraining {resource_type} model with {len(combined_features)} samples across {len(df_list)} instances.")
                self.trainer.train_isolation_forest(combined_features, resource_type)
            else:
                logger.info(f"Skipping {resource_type} retraining. Combined data size ({len(combined_features)}) < 500.")
                
        logger.info("ML Model Retraining cycle completed.")
