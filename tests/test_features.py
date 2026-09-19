import pytest
import pandas as pd
from datetime import datetime, timedelta, timezone
from ml.features import build_feature_vector

def generate_mock_data():
    """Generates 24 hours of mock CPU data (288 points at 5 min intervals)"""
    now = datetime.now(timezone.utc)
    data = []
    
    # 288 points = 24 hours. Value normally 10%
    for i in range(288, 0, -1):
        t = now - timedelta(minutes=5 * i)
        data.append({
            "time": t.isoformat(),
            "metric_name": "CPUUtilization",
            "value": 10.0
        })
        
    # The final point spikes to 90%
    data[-1]["value"] = 90.0
    
    return pd.DataFrame(data)

def test_build_feature_vector_ec2():
    df = generate_mock_data()
    
    features = build_feature_vector(df, "ec2")
    
    # Check shape
    assert len(features) == 1
    assert "rolling_avg_cpu_24h" in features.columns
    assert "cpu_deviation_ratio" in features.columns
    
    # The 24h average should be slightly above 10 (because of the one 90 spike)
    # The exact average is ((287 * 10) + 90) / 288 = 2960 / 288 = ~10.27
    avg_24h = features["rolling_avg_cpu_24h"].iloc[0]
    assert 10.0 < avg_24h < 11.0
    
    # The deviation ratio for the last point (90) against the avg (~10.27) should be ~8.7
    ratio = features["cpu_deviation_ratio"].iloc[0]
    assert 8.0 < ratio < 9.0
    
def test_build_feature_vector_empty():
    df = pd.DataFrame()
    features = build_feature_vector(df, "ec2")
    assert features.empty
