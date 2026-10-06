import asyncio
import logging
from datetime import datetime, timezone

import pandas as pd

from backend.app.config import settings
from backend.app.db.asyncpg_pool import get_pool
from backend.app.db.pagination import fetch_all
from backend.app.db.supabase_client import get_supabase_client
from ml.features import build_training_dataset
from ml.trainer import MIN_TRAINING_ROWS, ModelTrainer

logger = logging.getLogger(__name__)


class MLRetrainingService:
    def __init__(self):
        self.db = get_supabase_client()
        self.trainer = ModelTrainer()

    async def run(self):
        logger.info("Starting ML Model Retraining cycle...")
        pool = get_pool()
        resources = await asyncio.to_thread(
            fetch_all,
            lambda: self.db.table("resources")
            .select("id, resource_type")
            .not_.in_("state", ["terminated", "deleted"])
            .order("id"),
        )
        if not resources:
            logger.info("No active resources found. Skipping ML retraining.")
            return {}

        training_data_by_type: dict[str, list] = {}
        for r in resources:
            async with pool.acquire() as conn:
                records = await conn.fetch(
                    """
                    SELECT time, metric_name, value
                    FROM resource_metrics
                    WHERE resource_id = $1 AND time > NOW() - INTERVAL '30 days'
                    ORDER BY time ASC
                    """,
                    r["id"],
                )
            if not records:
                continue
            df = pd.DataFrame([dict(rec) for rec in records])
            # Gate on timestamps, not rows: EC2 writes 3 metric rows per sample.
            if df["time"].nunique() < settings.ML_MIN_POINTS_IF:
                continue
            features_df = await asyncio.to_thread(build_training_dataset, df, r["resource_type"])
            if not features_df.empty:
                training_data_by_type.setdefault(r["resource_type"], []).append(features_df)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        trained = {}
        for resource_type, df_list in training_data_by_type.items():
            # Keep time order (each resource's frame is time-ordered) so the
            # trainer's holdout is the most recent data.
            combined = pd.concat(df_list).sort_index().reset_index(drop=True)
            if len(combined) < MIN_TRAINING_ROWS:
                logger.info("Skipping %s retraining: %s samples < %s.", resource_type, len(combined), MIN_TRAINING_ROWS)
                continue
            if len(combined) > settings.ML_MAX_TRAINING_ROWS:
                # Bound memory/CPU on small instances: the most recent rows.
                combined = combined.tail(settings.ML_MAX_TRAINING_ROWS).reset_index(drop=True)
            logger.info("Retraining %s model with %s samples.", resource_type, len(combined))
            await asyncio.to_thread(self.trainer.train_isolation_forest, combined, resource_type, today)
            trained[resource_type] = dict(self.trainer.last_result)
        logger.info("ML Model Retraining cycle completed: %s", trained)
        return trained
