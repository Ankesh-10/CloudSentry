import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

def generate_from_scenario(scenario_path: str) -> tuple[pd.DataFrame, pd.Series]:
    """
    Reads a JSON scenario and generates a synthetic time-series metric DataFrame.
    Returns:
        df: DataFrame matching 'resource_metrics' format (time, metric_name, value)
        labels: Series of boolean labels indicating if the point is anomalous
    """
    with open(scenario_path, 'r') as f:
        config = json.load(f)
        
    total_points = config.get("total_points", 2000)
    resource_type = config.get("resource_type", "ec2")
    baseline_cfg = config.get("baseline", {})
    anomaly_cfg = config.get("anomaly", None)
    
    start_time = datetime.now(timezone.utc) - timedelta(minutes=5 * total_points)
    
    records = []
    labels = []
    
    for i in range(total_points):
        t = start_time + timedelta(minutes=5 * i)
        
        is_anomaly = False
        if anomaly_cfg:
            start = anomaly_cfg.get("start_point", -1)
            end = start + anomaly_cfg.get("duration", 0)
            if start <= i < end:
                is_anomaly = True
                
        labels.append(is_anomaly)
        
        cfg_to_use = anomaly_cfg if is_anomaly else baseline_cfg
        
        for metric, params in cfg_to_use.items():
            if metric in ["start_point", "duration"]:
                continue
                
            mean = params.get("mean", 0)
            std = params.get("std", 0)
            # Ensure no negative values for typical metrics
            val = max(0.0, np.random.normal(mean, std))
            
            records.append({
                "time": t,
                "metric_name": metric,
                "value": val
            })
            
    df = pd.DataFrame(records)
    # create labels series indexed by time (one label per time step)
    times = pd.date_range(start=start_time, periods=total_points, freq="5min")
    labels_series = pd.Series(labels, index=times)
    
    return df, labels_series
