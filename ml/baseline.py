import numpy as np
import pandas as pd


def detect_anomaly_zscore(df: pd.DataFrame, metric_name: str, threshold: float = 2.5) -> dict:
    if df.empty:
        return {"is_anomaly": False, "score": 0.0, "reason": "Insufficient data for Z-score"}

    metric_data = df[df["metric_name"] == metric_name]["value"].values
    if len(metric_data) < 12:
        return {"is_anomaly": False, "score": 0.0, "reason": "Insufficient data"}

    values = np.asarray(metric_data, dtype=float)
    std = float(values.std())
    if std <= 1e-9:
        return {"is_anomaly": False, "score": 0.0, "reason": "No variance in recent data"}
    latest_z = abs(float((values[-1] - values.mean()) / std))
    confidence = min(0.8, len(metric_data) / 288.0 * 0.8)
    is_anomaly = latest_z > threshold
    reason = ""
    if is_anomaly:
        reason = f"{metric_name} deviated significantly from recent average (Z={latest_z:.2f})"
    return {
        "is_anomaly": is_anomaly,
        "score": latest_z,
        "confidence": float(confidence),
        "reason": reason,
        "anomaly_type": "unusual_cpu_spike" if metric_name == "CPUUtilization" else "statistical_anomaly",
        "severity": "LOW",
    }


def detect_anomaly_ewma(df: pd.DataFrame, metric_name: str, span: int = 24, threshold: float = 2.5) -> dict:
    metric_df = df[df["metric_name"] == metric_name].sort_values("time")
    if len(metric_df) < 24:
        return detect_anomaly_zscore(df, metric_name, threshold=threshold)

    values = metric_df["value"].astype(float)
    ewma = values.ewm(span=span, adjust=False).mean()
    ewmstd = values.ewm(span=span, adjust=False).std().fillna(0.0)
    latest = float(values.iloc[-1])
    mean = float(ewma.iloc[-1])
    std = float(ewmstd.iloc[-1])
    if std <= 1e-9:
        return {"is_anomaly": False, "score": 0.0, "reason": "EWMA variance too small", "anomaly_type": "statistical_anomaly", "severity": "LOW"}
    z = abs((latest - mean) / std)
    is_anomaly = z > threshold
    return {
        "is_anomaly": is_anomaly,
        "score": float(z),
        "confidence": min(0.85, z / 6.0),
        "reason": f"{metric_name} deviated from EWMA baseline (Z={z:.2f})" if is_anomaly else "",
        "anomaly_type": "unusual_cpu_spike" if metric_name == "CPUUtilization" else "statistical_anomaly",
        "severity": "MEDIUM" if z > 4 else "LOW",
        "model_version": "ewma_fallback",
    }


def check_idle_compute(df: pd.DataFrame, resource_type: str, cpu_threshold: float = 5.0, window_hours: float = 2.0) -> dict:
    """`evaluable` is False when there is not enough data to decide either way,
    so callers do not resolve an existing idle anomaly on missing data."""
    if resource_type != "ec2":
        return {"is_anomaly": False, "evaluable": False}

    needed = max(int(window_hours * 12), 12)
    cpu_data = df[df["metric_name"] == "CPUUtilization"].sort_values("time")
    net_data = df[df["metric_name"] == "NetworkIn"].sort_values("time")
    if len(cpu_data) < needed:
        return {"is_anomaly": False, "evaluable": False}

    cpu_window = cpu_data["value"].tail(needed)
    max_cpu = float(cpu_window.max())
    avg_cpu = float(cpu_window.mean())
    net_bytes = float(net_data["value"].tail(needed).sum()) if not net_data.empty else 0.0
    # NetworkIn is bytes per 5-min period average; treat < 1MB over the window as negligible.
    if max_cpu < cpu_threshold and net_bytes < 1_000_000:
        idle_score = max(0.0, min(1.0, 1.0 - (avg_cpu / 100.0)))
        return {
            "is_anomaly": True,
            "score": idle_score,
            "confidence": 0.85,
            "reason": (
                f"EC2 idle for {window_hours:.0f}h (max CPU {max_cpu:.2f}% < {cpu_threshold}%, "
                f"network {net_bytes:.0f} bytes)"
            ),
            "anomaly_type": "idle_compute",
            "severity": "MEDIUM",
            "features": {
                "rolling_avg_cpu_24h": avg_cpu,
                "idle_score": idle_score,
            },
        }
    return {"is_anomaly": False}


def check_runaway_lambda(df: pd.DataFrame, resource_type: str, spike_ratio: float = 10.0, error_rate_threshold: float = 0.20) -> dict:
    if resource_type != "lambda":
        return {"is_anomaly": False}
    inv = df[df["metric_name"] == "Invocations"].sort_values("time")
    err = df[df["metric_name"] == "Errors"].sort_values("time")
    if len(inv) < 24:
        return {"is_anomaly": False}
    baseline = float(inv["value"].iloc[:-3].mean()) if len(inv) > 3 else float(inv["value"].mean())
    current = float(inv["value"].iloc[-1])
    ratio = current / baseline if baseline > 0 else 0.0
    errors = float(err["value"].iloc[-1]) if not err.empty else 0.0
    error_rate = errors / (current + 1.0)
    if ratio >= spike_ratio or error_rate > error_rate_threshold:
        return {
            "is_anomaly": True,
            "score": max(ratio / 10.0, error_rate),
            "confidence": min(0.95, 0.6 + ratio / 50.0),
            "reason": f"Lambda invocations {ratio:.1f}× baseline ({current:.0f} vs {baseline:.0f}); error_rate={error_rate:.2f}",
            "anomaly_type": "runaway_lambda",
            "severity": "HIGH",
            "features": {"invocation_spike_ratio": ratio, "error_rate": error_rate},
        }
    return {"is_anomaly": False}
