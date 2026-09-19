import pandas as pd
import numpy as np
from scipy import stats

def detect_anomaly_zscore(df: pd.DataFrame, metric_name: str, threshold: float = 3.0) -> dict:
    """
    Cold-start anomaly detection using Z-score.
    Calculates Z-score for the latest data point based on historical data.
    """
    if df.empty or len(df) < 12: # Need at least 1 hour of data (5-min intervals)
        return {"is_anomaly": False, "score": 0.0, "reason": "Insufficient data for Z-score"}
        
    metric_data = df[df['metric_name'] == metric_name]['value'].values
    if len(metric_data) < 12:
        return {"is_anomaly": False, "score": 0.0, "reason": "Insufficient data"}
        
    # Calculate Z-scores
    z_scores = stats.zscore(metric_data)
    latest_z = abs(z_scores[-1])
    
    # Calculate confidence based on data size (max confidence 0.8 for baseline)
    confidence = min(0.8, len(metric_data) / 288.0 * 0.8) 
    
    is_anomaly = latest_z > threshold
    
    reason = ""
    if is_anomaly:
        reason = f"{metric_name} deviated significantly from recent average (Z={latest_z:.2f})"
        
    return {
        "is_anomaly": is_anomaly,
        "score": float(latest_z),
        "confidence": float(confidence),
        "reason": reason
    }
    
def check_idle_compute(df: pd.DataFrame, resource_type: str) -> dict:
    """
    Rule-based check for idle resources (e.g. EC2 CPU < 2% for 24h).
    """
    if resource_type != 'ec2':
        return {"is_anomaly": False}
        
    cpu_data = df[df['metric_name'] == 'CPUUtilization']
    if len(cpu_data) < 288: # Need 24h
        return {"is_anomaly": False}
        
    max_cpu_24h = cpu_data['value'].tail(288).max()
    
    if max_cpu_24h < 2.0:
        return {
            "is_anomaly": True,
            "score": 1.0,
            "confidence": 0.95,
            "reason": f"EC2 instance has been idle for 24 hours (Max CPU: {max_cpu_24h:.2f}%)",
            "anomaly_type": "idle_compute",
            "severity": "MEDIUM"
        }
        
    return {"is_anomaly": False}
