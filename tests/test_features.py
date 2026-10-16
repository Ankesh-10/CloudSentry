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


def _cpu(values, step=timedelta(minutes=5), metric="CPUUtilization"):
    now = datetime.now(timezone.utc)
    return pd.DataFrame([{"time": now - step * (len(values) - i), "metric_name": metric, "value": v}
                         for i, v in enumerate(values)])


def test_only_a_cpu_rise_is_a_spike():
    rise = detect_anomaly_zscore(_cpu([20.0, 22.0] * 15 + [90.0]), "CPUUtilization")
    drop = detect_anomaly_zscore(_cpu([60.0, 62.0] * 15 + [5.0]), "CPUUtilization")
    assert (rise["is_anomaly"], rise["anomaly_type"], rise["direction"]) == (True, "unusual_cpu_spike", "up")
    assert (drop["is_anomaly"], drop["anomaly_type"], drop["direction"]) == (True, "statistical_anomaly", "down")
    assert "fell well below" in drop["reason"]
    ewma = detect_anomaly_ewma(_cpu([60.0, 62.0] * 20 + [5.0]), "CPUUtilization")
    assert (ewma["anomaly_type"], ewma["direction"]) == ("statistical_anomaly", "down")


def test_idle_window_is_measured_in_time_not_samples():
    # 30 s samples: 13 of them cover a 6-minute window, 6 do not (the old rule wanted 12 of any age).
    assert check_idle_compute(_cpu([1.0] * 13, step=timedelta(seconds=30)), "ec2", window_hours=0.1)["is_anomaly"]
    short = check_idle_compute(_cpu([1.0] * 6, step=timedelta(seconds=30)), "ec2", window_hours=0.1)
    assert short == {"is_anomaly": False, "evaluable": False}
    # 12 samples at 5 min cover one hour, not the two the window asks for.
    assert check_idle_compute(_cpu([1.0] * 12), "ec2", window_hours=2.0)["evaluable"] is False
    assert "6 min" in check_idle_compute(_cpu([1.0] * 13, step=timedelta(seconds=30)), "ec2",
                                         window_hours=0.1)["reason"]


def test_runaway_lambda_stays_runaway_while_it_lasts():
    """Regression: the mean baseline absorbed the runaway within minutes."""
    df = _cpu([100.0] * 30 + [5000.0] * 12, metric="Invocations")
    result = check_runaway_lambda(df, "lambda")
    assert result["is_anomaly"] is True and result["anomaly_type"] == "runaway_lambda"


def test_idle_database_needs_no_connections_for_the_whole_window():
    from ml.baseline import check_idle_database
    quiet = _cpu([0.0] * 13, step=timedelta(seconds=30), metric="DatabaseConnections")
    busy = _cpu([0.0] * 12 + [3.0], step=timedelta(seconds=30), metric="DatabaseConnections")
    assert check_idle_database(quiet, window_hours=0.1)["anomaly_type"] == "idle_database"
    assert check_idle_database(busy, window_hours=0.1) == {"is_anomaly": False}
    assert check_idle_database(quiet.tail(4), window_hours=0.1)["evaluable"] is False


def test_low_storage_is_a_share_of_the_allocated_size():
    from ml.baseline import GIB, check_low_storage
    df = _cpu([14 * GIB, 1.5 * GIB], metric="FreeStorageSpace")
    result = check_low_storage(df, 20, min_free_pct=10)  # 1.5 of 20 GiB = 7.5 %
    assert (result["anomaly_type"], result["severity"]) == ("low_storage", "MEDIUM")
    assert check_low_storage(_cpu([0.5 * GIB], metric="FreeStorageSpace"), 20)["severity"] == "HIGH"
    assert check_low_storage(_cpu([14 * GIB], metric="FreeStorageSpace"), 20) == {"is_anomaly": False}
    assert check_low_storage(df, None)["evaluable"] is False  # size unknown until rediscovery
