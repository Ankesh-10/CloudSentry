import pandas as pd
from datetime import datetime, timedelta, timezone

from ml.baseline import check_idle_compute, check_runaway_lambda, detect_anomaly_ewma, detect_anomaly_zscore
from ml.features import build_feature_vector


def generate_mock_data():
    now = datetime.now(timezone.utc)
    data = []
    for i in range(288, 0, -1):
        t = now - timedelta(minutes=5 * i)
        data.append({"time": t.isoformat(), "metric_name": "CPUUtilization", "value": 10.0})
    data[-1]["value"] = 90.0
    return pd.DataFrame(data)


def test_build_feature_vector_ec2():
    df = generate_mock_data()
    features = build_feature_vector(df, "ec2")
    assert len(features) == 1
    assert "rolling_avg_cpu_24h" in features.columns
    avg_24h = features["rolling_avg_cpu_24h"].iloc[0]
    assert 10.0 < avg_24h < 11.0
    ratio = features["cpu_deviation_ratio"].iloc[0]
    assert 8.0 < ratio < 9.0


def test_build_feature_vector_empty():
    df = pd.DataFrame()
    features = build_feature_vector(df, "ec2")
    assert features.empty


def test_build_feature_vector_duplicate_timestamps():
    now = datetime.now(timezone.utc)
    df = pd.DataFrame([
        {"time": now, "metric_name": "CPUUtilization", "value": 1.0},
        {"time": now, "metric_name": "CPUUtilization", "value": 2.0},
        {"time": now, "metric_name": "NetworkIn", "value": 10.0},
    ])
    features = build_feature_vector(df, "ec2")
    assert len(features) == 1


def test_zscore_flags_outlier():
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(30, 0, -1):
        rows.append({"time": now - timedelta(minutes=5 * i), "metric_name": "CPUUtilization", "value": 10.0})
    rows[-1]["value"] = 90.0
    result = detect_anomaly_zscore(pd.DataFrame(rows), "CPUUtilization", threshold=2.5)
    assert result["is_anomaly"] is True


def test_idle_compute_requires_low_cpu_and_network():
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(24, 0, -1):
        t = now - timedelta(minutes=5 * i)
        rows.append({"time": t, "metric_name": "CPUUtilization", "value": 1.0})
        rows.append({"time": t, "metric_name": "NetworkIn", "value": 10.0})
    result = check_idle_compute(pd.DataFrame(rows), "ec2", cpu_threshold=5.0, window_hours=2.0)
    assert result["is_anomaly"] is True
    assert result["anomaly_type"] == "idle_compute"
    assert result["features"]["idle_score"] > 0.8


def test_runaway_lambda_spike():
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(30, 0, -1):
        t = now - timedelta(minutes=5 * i)
        inv = 8500 if i <= 2 else 100
        rows.append({"time": t, "metric_name": "Invocations", "value": inv})
        rows.append({"time": t, "metric_name": "Errors", "value": 0})
    result = check_runaway_lambda(pd.DataFrame(rows), "lambda")
    assert result["is_anomaly"] is True
    assert result["anomaly_type"] == "runaway_lambda"


def test_ewma_on_short_series_falls_back():
    now = datetime.now(timezone.utc)
    rows = [{"time": now - timedelta(minutes=5 * i), "metric_name": "CPUUtilization", "value": 10.0} for i in range(10, 0, -1)]
    result = detect_anomaly_ewma(pd.DataFrame(rows), "CPUUtilization")
    assert "is_anomaly" in result
