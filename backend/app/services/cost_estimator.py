import json
import logging
import os
from datetime import datetime, timezone
from backend.app.db.supabase_client import get_supabase_client
from backend.app.config import settings

logger = logging.getLogger(__name__)

class CostEstimationService:
    def __init__(self):
        self.db = get_supabase_client()
        region = settings.AWS_DEFAULT_REGION.replace("-", "_")
        
        # Try to load the region-specific file, fallback to us-east-1
        base_path = os.path.join(os.path.dirname(__file__), "../../../data/pricing")
        pricing_path = os.path.join(base_path, f"aws_{region}.json")
        
        if not os.path.exists(pricing_path):
            logger.warning(f"Pricing file for {region} not found. Falling back to us-east-1.")
            pricing_path = os.path.join(base_path, "aws_us_east_1.json")
            
        try:
            with open(pricing_path, "r") as f:
                self.pricing = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load pricing JSON: {e}")
            self.pricing = {}
            
    def run(self):
        """
        Calculates estimated cost for all resources based on static pricing.
        This is a simplified stub for the MVP.
        """
        logger.info("Starting Cost Estimation cycle...")
        if not self.pricing:
            return
            
        res = self.db.table("resources").select("id, resource_type, metadata").execute()
        resources = res.data
        
        now = datetime.now(timezone.utc).isoformat()
        cost_records = []
        
        for r in resources:
            cost = 0.0
            if r["resource_type"] == "ec2":
                inst_type = r.get("metadata", {}).get("instance_type", "t2.micro")
                rate = self.pricing.get("ec2", {}).get(inst_type, 0.0)
                cost = rate  # hourly rate
                
            if cost > 0:
                cost_records.append({
                    "resource_id": r["id"],
                    "estimated_cost_usd": cost,
                    "source": "estimated",
                    "recorded_at": now
                })
                
        if cost_records:
            self.db.table("cost_records").insert(cost_records).execute()
            logger.info(f"Inserted {len(cost_records)} cost records.")
