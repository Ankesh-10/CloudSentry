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
    signed_z = float((values[-1] - values.mean()) / std)
    latest_z = abs(signed_z)
    confidence = min(0.8, len(metric_data) / 288.0 * 0.8)
    is_anomaly = latest_z > threshold
    direction = "up" if signed_z > 0 else "down"
    reason = ""
    if is_anomaly:
        reason = f"{metric_name} {_moved(direction)} its recent average (Z={latest_z:.2f})"
    return {
        "is_anomaly": is_anomaly,
        "score": latest_z,
        "confidence": float(confidence),
        "reason": reason,
        "anomaly_type": _statistical_type(metric_name, direction),
        "severity": "LOW",
        "direction": direction,
    }


def _statistical_type(metric_name: str, direction: str) -> str:
    # Only a rise is a "spike"; a CPU drop used to be reported as one.
    return "unusual_cpu_spike" if metric_name == "CPUUtilization" and direction == "up" else "statistical_anomaly"


def _moved(direction: str) -> str:
    return "rose well above" if direction == "up" else "fell well below"


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
    signed_z = (latest - mean) / std
    z = abs(signed_z)
    is_anomaly = z > threshold
    direction = "up" if signed_z > 0 else "down"
    return {
        "is_anomaly": is_anomaly,
        "score": float(z),
        "confidence": min(0.85, z / 6.0),
        "reason": f"{metric_name} {_moved(direction)} its EWMA baseline (Z={z:.2f})" if is_anomaly else "",
        "anomaly_type": _statistical_type(metric_name, direction),
        "severity": "MEDIUM" if z > 4 else "LOW",
        "model_version": "ewma_fallback",
        "direction": direction,
    }


IDLE_MIN_SAMPLES = 6
IDLE_MIN_COVERAGE = 0.9  # of the window that the samples must span


def _window_label(hours: float) -> str:
    return f"{hours:g}h" if hours >= 1 else f"{hours * 60:g} min"


def check_idle_compute(df: pd.DataFrame, resource_type: str, cpu_threshold: float = 5.0, window_hours: float = 2.0) -> dict:
    """Idle = every CPU sample of the last `window_hours` (by time, whatever the
    sampling period) under `cpu_threshold`, and negligible network. `evaluable`
    is False when the samples do not cover the window, so callers do not
    resolve an existing idle anomaly on missing data."""
    if resource_type != "ec2":
        return {"is_anomaly": False, "evaluable": False}

    cpu_data = df[df["metric_name"] == "CPUUtilization"].sort_values("time")
    if cpu_data.empty:
        return {"is_anomaly": False, "evaluable": False}
    times = pd.to_datetime(cpu_data["time"], utc=True)
    start = times.iloc[-1] - pd.Timedelta(hours=window_hours)
    in_window = cpu_data[(times > start).values]
    span = times.iloc[-1] - times[(times > start).values].iloc[0]
    if len(in_window) < IDLE_MIN_SAMPLES or span < pd.Timedelta(hours=window_hours * IDLE_MIN_COVERAGE):
        return {"is_anomaly": False, "evaluable": False}

    net_data = df[df["metric_name"] == "NetworkIn"]
    net_window = net_data[(pd.to_datetime(net_data["time"], utc=True) > start).values]
    max_cpu = float(in_window["value"].max())
    avg_cpu = float(in_window["value"].mean())
    net_bytes = float(net_window["value"].sum()) if not net_window.empty else 0.0
    # NetworkIn is bytes per sample period; treat < 1MB over the window as negligible.
    if max_cpu < cpu_threshold and net_bytes < 1_000_000:
        idle_score = max(0.0, min(1.0, 1.0 - (avg_cpu / 100.0)))
        return {
            "is_anomaly": True,
            "score": idle_score,
            "confidence": 0.85,
            "reason": (
                f"EC2 idle for {_window_label(window_hours)} (max CPU {max_cpu:.2f}% < {cpu_threshold}%, "
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
    # Median, not mean: a runaway that keeps going would otherwise lift its own
    # baseline within minutes and stop counting as one.
    baseline = float(inv["value"].iloc[:-3].median()) if len(inv) > 3 else float(inv["value"].median())
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
