import os
import asyncio
import random
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

async def inject_anomaly():
    """
    Injects fake metric spikes into the resource_metrics table 
    to trigger the cold-start Z-score logic.
    """
    load_dotenv()
    
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
        return
        
    supabase: Client = create_client(url, key)
    
    # Get a random active EC2 instance
    res = supabase.table("resources").select("id").eq("resource_type", "ec2").limit(1).execute()
    if not res.data:
        print("No EC2 instances found in the database. Please run discovery first.")
        return
        
    resource_id = res.data[0]["id"]
    print(f"Injecting anomaly into resource {resource_id}...")
    
    now = datetime.now(timezone.utc)
    
    # Inject 11 normal data points (avg 20% CPU)
    # Then 1 massive spike (avg 95% CPU)
    
    insert_payload = []
    
    for i in range(12, 0, -1):
        t = now - timedelta(minutes=5 * i)
        
        # If it's the last point, make it a massive spike
        if i == 1:
            value = random.uniform(90.0, 99.0)
        else:
            value = random.uniform(15.0, 25.0)
            
        insert_payload.append({
            "time": t.isoformat(),
            "resource_id": resource_id,
            "metric_name": "CPUUtilization",
            "value": value,
            "unit": "Percent"
        })
        
    res = supabase.table("resource_metrics").insert(insert_payload).execute()
    print(f"Injected {len(insert_payload)} metric points.")
    print("The anomaly detector Z-score logic should pick this up on the next cycle!")

if __name__ == "__main__":
    asyncio.run(inject_anomaly())
