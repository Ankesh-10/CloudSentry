import numpy as np
import pandas as pd


def _pivot_metrics(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["time"] = pd.to_datetime(work["time"], utc=True)
    work = work.drop_duplicates(subset=["time", "metric_name"], keep="last")
    pivoted = work.pivot(index="time", columns="metric_name", values="value").sort_index()
    return pivoted.ffill().fillna(0.0)


def _fill_expected(features: pd.DataFrame) -> pd.DataFrame:
    expected_cols = [
        "time_of_day",
        "rolling_avg_cpu_1h",
        "rolling_avg_cpu_24h",
        "cpu_deviation_ratio",
        "network_in_ratio",
        "invocation_spike_ratio",
        "error_rate",
        "hours_unattached",
        "estimated_cost_per_hour",
    ]
    for col in expected_cols:
        if col not in features.columns:
            features[col] = 0.0
    return features[expected_cols]


def build_feature_vector(df: pd.DataFrame, resource_type: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    df_pivot = _pivot_metrics(df)
    if df_pivot.empty:
        return pd.DataFrame()

    features = pd.DataFrame(index=[df_pivot.index[-1]])
    metrics = df_pivot.columns
    features["time_of_day"] = df_pivot.index[-1].hour

    if resource_type == "ec2":
        cpu = df_pivot["CPUUtilization"] if "CPUUtilization" in metrics else pd.Series(0.0, index=df_pivot.index)
        net_in = df_pivot["NetworkIn"] if "NetworkIn" in metrics else pd.Series(0.0, index=df_pivot.index)
        rolling_cpu_1h = cpu.rolling(window=12, min_periods=1).mean()
        rolling_cpu_24h = cpu.rolling(window=288, min_periods=1).mean()
        features["rolling_avg_cpu_1h"] = rolling_cpu_1h.iloc[-1]
        features["rolling_avg_cpu_24h"] = rolling_cpu_24h.iloc[-1]
        avg_24_cpu = rolling_cpu_24h.iloc[-1]
        features["cpu_deviation_ratio"] = cpu.iloc[-1] / avg_24_cpu if avg_24_cpu > 0 else 0.0
        rolling_net_24h = net_in.rolling(window=288, min_periods=1).mean()
        avg_24_net = rolling_net_24h.iloc[-1]
        features["network_in_ratio"] = net_in.iloc[-1] / avg_24_net if avg_24_net > 0 else 0.0
    elif resource_type == "lambda":
        invocations = df_pivot["Invocations"] if "Invocations" in metrics else pd.Series(0.0, index=df_pivot.index)
        errors = df_pivot["Errors"] if "Errors" in metrics else pd.Series(0.0, index=df_pivot.index)
        rolling_inv_24h = invocations.rolling(window=288, min_periods=1).mean()
        avg_24_inv = rolling_inv_24h.iloc[-1]
        features["invocation_spike_ratio"] = invocations.iloc[-1] / avg_24_inv if avg_24_inv > 0 else 0.0
        features["error_rate"] = errors.iloc[-1] / (invocations.iloc[-1] + 1.0)

    return _fill_expected(features)


def build_training_dataset(df: pd.DataFrame, resource_type: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    df_pivot = _pivot_metrics(df)
    if df_pivot.empty:
        return pd.DataFrame()

    features = pd.DataFrame(index=df_pivot.index)
    metrics = df_pivot.columns
    features["time_of_day"] = df_pivot.index.hour

    if resource_type == "ec2":
        cpu = df_pivot["CPUUtilization"] if "CPUUtilization" in metrics else pd.Series(0.0, index=df_pivot.index)
        net_in = df_pivot["NetworkIn"] if "NetworkIn" in metrics else pd.Series(0.0, index=df_pivot.index)
        rolling_cpu_1h = cpu.rolling(window=12, min_periods=1).mean()
        rolling_cpu_24h = cpu.rolling(window=288, min_periods=1).mean()
        features["rolling_avg_cpu_1h"] = rolling_cpu_1h
        features["rolling_avg_cpu_24h"] = rolling_cpu_24h
        safe_avg_24_cpu = rolling_cpu_24h.replace(0, np.nan)
        features["cpu_deviation_ratio"] = (cpu / safe_avg_24_cpu).fillna(0.0)
        rolling_net_24h = net_in.rolling(window=288, min_periods=1).mean()
        safe_avg_24_net = rolling_net_24h.replace(0, np.nan)
        features["network_in_ratio"] = (net_in / safe_avg_24_net).fillna(0.0)
    elif resource_type == "lambda":
        invocations = df_pivot["Invocations"] if "Invocations" in metrics else pd.Series(0.0, index=df_pivot.index)
        errors = df_pivot["Errors"] if "Errors" in metrics else pd.Series(0.0, index=df_pivot.index)
        rolling_inv_24h = invocations.rolling(window=288, min_periods=1).mean()
        safe_avg_24_inv = rolling_inv_24h.replace(0, np.nan)
        features["invocation_spike_ratio"] = (invocations / safe_avg_24_inv).fillna(0.0)
        features["error_rate"] = (errors / (invocations + 1.0)).fillna(0.0)

    features = _fill_expected(features)
    if len(features) > 288:
        features = features.iloc[288:]
    return features
