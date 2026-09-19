import logging
import datetime
from typing import List, Dict, Any
from backend.app.adapters.aws import AWSAdapter
from backend.app.db.supabase_client import get_supabase_client
from backend.app.db.asyncpg_pool import get_pool

logger = logging.getLogger(__name__)

class TelemetryService:
    def __init__(self):
        self.cloud_adapter = AWSAdapter()
        self.db = get_supabase_client()
        
    async def run(self):
        """
        Main entrypoint for APScheduler job.
        Fetches metrics for active resources and inserts into resource_metrics via asyncpg.
        """
        logger.info("Starting Telemetry Collection cycle...")
        
        # 1. Get active resources
        res = self.db.table("resources").select("id, provider_id, resource_type").neq("state", "terminated").execute()
        resources = res.data
        if not resources:
            logger.info("No active resources found.")
            return
            
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(minutes=5)
        
        queries = []
        mapping = {} # query_id -> (resource_id, metric_name)
        
        query_idx = 0
        for r in resources:
            if r["resource_type"] == "ec2":
                for metric in ["CPUUtilization", "NetworkIn", "NetworkOut"]:
                    qid = f"q_{query_idx}"
                    queries.append({
                        "Id": qid,
                        "MetricStat": {
                            "Metric": {
                                "Namespace": "AWS/EC2",
                                "MetricName": metric,
                                "Dimensions": [{"Name": "InstanceId", "Value": r["provider_id"]}]
                            },
                            "Period": 300,
                            "Stat": "Average"
                        },
                        "ReturnData": True
                    })
                    mapping[qid] = (r["id"], metric)
                    query_idx += 1
                    
        if not queries:
            return

        # Fetch in batches of 500
        metric_data = self.cloud_adapter.get_metric_data(queries, start_time, end_time)
        
        insert_records = []
        for result in metric_data:
            qid = result["Id"]
            timestamps = result.get("Timestamps", [])
            values = result.get("Values", [])
            
            if not timestamps:
                continue
                
            resource_id, metric_name = mapping[qid]
            for t, v in zip(timestamps, values):
                insert_records.append((t, resource_id, metric_name, float(v), None))
                
        if insert_records:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.copy_records_to_table(
                    'resource_metrics',
                    columns=['time', 'resource_id', 'metric_name', 'value', 'unit'],
                    records=insert_records
                )
            logger.info(f"Inserted {len(insert_records)} metric records.")
        else:
            logger.info("No metric data returned from CloudWatch.")
