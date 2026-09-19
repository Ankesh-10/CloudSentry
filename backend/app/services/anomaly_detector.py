import logging
import pandas as pd
from datetime import datetime, timezone
from backend.app.db.supabase_client import get_supabase_client
from backend.app.db.asyncpg_pool import get_pool
from ml.features import build_feature_vector
from ml.baseline import detect_anomaly_zscore, check_idle_compute
from ml.inference import InferenceEngine

logger = logging.getLogger(__name__)

class AnomalyDetectorService:
    def __init__(self):
        self.db = get_supabase_client()
        self.inference_engine = InferenceEngine()
        
    async def run(self):
        """
        Main entrypoint for APScheduler job.
        Fetches recent metrics, engineers features, and detects anomalies.
        """
        logger.info("Starting Anomaly Detection cycle...")
        pool = get_pool()
        
        # 1. Get active resources
        res = self.db.table("resources").select("id, resource_type").neq("state", "terminated").execute()
        resources = res.data
        if not resources:
            return
            
        now = datetime.now(timezone.utc).isoformat()
        
        for r in resources:
            resource_id = r["id"]
            resource_type = r["resource_type"]
            
            # 2. Fetch last 24h metrics for this resource via asyncpg
            query = """
                SELECT time, metric_name, value 
                FROM resource_metrics 
                WHERE resource_id = $1 AND time > NOW() - INTERVAL '24 hours'
                ORDER BY time ASC
            """
            async with pool.acquire() as conn:
                records = await conn.fetch(query, resource_id)
                
            if not records:
                continue
                
            df = pd.DataFrame([dict(rec) for rec in records])
            
            # 3. Rule-based checks (Idle compute)
            idle_result = check_idle_compute(df, resource_type)
            if idle_result["is_anomaly"]:
                self._record_anomaly(resource_id, idle_result, now, {})
                continue
                
            # 4. Feature Engineering
            features_df = build_feature_vector(df, resource_type)
            if features_df.empty:
                continue
                
            # 5. ML Inference vs Baseline (Cold-start gate)
            ml_result = self.inference_engine.predict(features_df, resource_type)
            
            if ml_result["reason"] == "Model not trained":
                # Fallback to Baseline Z-score
                # For MVP, check CPU deviation
                if resource_type == 'ec2':
                    baseline_result = detect_anomaly_zscore(df, 'CPUUtilization')
                    if baseline_result["is_anomaly"]:
                        baseline_result["anomaly_type"] = "unusual_cpu_spike"
                        baseline_result["severity"] = "LOW"
                        self._record_anomaly(resource_id, baseline_result, now, features_df.iloc[-1].to_dict())
            else:
                # Use ML result
                if ml_result["is_anomaly"]:
                    ml_result["anomaly_type"] = "ml_behavioral_anomaly"
                    ml_result["severity"] = "MEDIUM" if ml_result["confidence"] < 0.8 else "HIGH"
                    self._record_anomaly(resource_id, ml_result, now, features_df.iloc[-1].to_dict())
                    
        logger.info("Anomaly Detection cycle completed.")
        
    def _record_anomaly(self, resource_id: str, result: dict, detected_at: str, features: dict):
        payload = {
            "resource_id": resource_id,
            "anomaly_type": result.get("anomaly_type", "unknown"),
            "severity": result.get("severity", "LOW"),
            "anomaly_score": result.get("score", 0.0),
            "confidence": result.get("confidence", 0.0),
            "detected_at": detected_at,
            "reason": result.get("reason", ""),
            "features_snapshot": features,
            "model_version": "baseline" if "zscore" in result.get("reason", "").lower() or "idle" in result.get("reason", "").lower() else "if_latest",
            "status": "active"
        }
        self.db.table("anomalies").insert(payload).execute()
        logger.info(f"Recorded anomaly for resource {resource_id}: {payload['anomaly_type']}")
