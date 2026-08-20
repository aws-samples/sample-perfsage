import json

import numpy as np
from strands import tool


@tool
def analyze_metrics(metrics_json: str) -> str:
    """Perform statistical analysis on ingested load test metrics.

    Analyzes latency distribution, throughput patterns, error clustering,
    and identifies degradation points where performance broke down.
    """
    try:
        metrics = json.loads(metrics_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid metrics JSON: {str(e)}"})

    latencies = np.array(metrics.get("latency_over_time", []))
    rps_series = np.array(metrics.get("rps_over_time", []))
    errors_series = np.array(metrics.get("errors_over_time", []))

    analysis = {
        "latency_analysis": _analyze_latency(latencies),
        "throughput_analysis": _analyze_throughput(rps_series),
        "error_analysis": _analyze_errors(errors_series, metrics.get("error_codes", {})),
        "degradation_point": _find_degradation_point(latencies, rps_series, errors_series),
        "summary_stats": {
            "total_requests": metrics.get("total_requests", 0),
            "error_rate_percent": round(metrics.get("error_rate", 0) * 100, 2),
            "p50_ms": metrics.get("latency", {}).get("p50_ms", 0),
            "p90_ms": metrics.get("latency", {}).get("p90_ms", 0),
            "p99_ms": metrics.get("latency", {}).get("p99_ms", 0),
            "rps_mean": metrics.get("rps_mean", 0),
            "rps_max": metrics.get("rps_max", 0),
            "duration_seconds": metrics.get("duration_seconds", 0),
            "vus_max": metrics.get("vus_max", 0),
        },
    }

    return json.dumps(analysis)


def _analyze_latency(latencies: np.ndarray) -> dict:
    if len(latencies) == 0:
        return {"status": "no_data"}

    skewness = _skewness(latencies) if len(latencies) > 2 else 0.0
    kurtosis = _kurtosis_excess(latencies) if len(latencies) > 3 else 0.0
    cv = float(np.std(latencies) / np.mean(latencies)) if np.mean(latencies) > 0 else 0.0

    tail_ratio = float(np.percentile(latencies, 99) / np.percentile(latencies, 50)) if np.percentile(latencies, 50) > 0 else 0.0

    return {
        "distribution": {
            "skewness": round(skewness, 3),
            "kurtosis": round(kurtosis, 3),
            "coefficient_of_variation": round(cv, 3),
            "tail_ratio_p99_p50": round(tail_ratio, 2),
        },
        "stability": "stable" if cv < 0.5 else "moderate_variance" if cv < 1.0 else "high_variance",
        "tail_severity": "normal" if tail_ratio < 3 else "heavy_tail" if tail_ratio < 10 else "extreme_tail",
    }


def _analyze_throughput(rps_series: np.ndarray) -> dict:
    if len(rps_series) == 0:
        return {"status": "no_data"}

    window = max(1, len(rps_series) // 10)
    rolling_mean = np.convolve(rps_series, np.ones(window) / window, mode="valid")

    trend = "stable"
    if len(rolling_mean) > 1:
        slope = (rolling_mean[-1] - rolling_mean[0]) / len(rolling_mean)
        if slope < -0.5:
            trend = "declining"
        elif slope > 0.5:
            trend = "increasing"

    return {
        "mean_rps": round(float(np.mean(rps_series)), 1),
        "max_rps": round(float(np.max(rps_series)), 1),
        "min_rps": round(float(np.min(rps_series)), 1),
        "trend": trend,
        "sustained_capacity": round(float(np.percentile(rps_series, 10)), 1),
    }


def _analyze_errors(errors_series: np.ndarray, error_codes: dict) -> dict:
    if len(errors_series) == 0:
        return {"status": "no_data"}

    total_errors = int(np.sum(errors_series))
    if total_errors == 0:
        return {"status": "no_errors", "total_errors": 0}

    non_zero = np.nonzero(errors_series)[0]
    burst_detected = False
    if len(non_zero) > 1:
        diffs = np.diff(non_zero)
        burst_detected = bool(np.any(diffs == 1) and np.max(errors_series) > np.mean(errors_series) * 3)

    return {
        "total_errors": total_errors,
        "error_distribution": error_codes,
        "burst_detected": burst_detected,
        "error_concentration": "early" if non_zero[0] < len(errors_series) * 0.3 else "late" if non_zero[-1] > len(errors_series) * 0.7 else "distributed",
    }


def _find_degradation_point(latencies: np.ndarray, rps_series: np.ndarray, errors_series: np.ndarray) -> dict:
    if len(latencies) == 0 or len(rps_series) == 0:
        return {"detected": False}

    window = max(1, len(latencies) // 20)
    rolling_latency = np.convolve(latencies, np.ones(window) / window, mode="valid")

    if len(rolling_latency) < 2:
        return {"detected": False}

    baseline = np.mean(rolling_latency[: max(1, len(rolling_latency) // 5)])
    threshold = baseline * 2

    degradation_indices = np.where(rolling_latency > threshold)[0]
    if len(degradation_indices) == 0:
        return {"detected": False}

    degrade_idx = int(degradation_indices[0])
    progress = degrade_idx / len(rolling_latency)
    rps_at_degrade = float(rps_series[min(degrade_idx, len(rps_series) - 1)]) if len(rps_series) > 0 else 0

    return {
        "detected": True,
        "at_percent_through_test": round(progress * 100, 1),
        "rps_at_degradation": round(rps_at_degrade, 1),
        "baseline_latency_ms": round(float(baseline), 1),
        "latency_at_degradation_ms": round(float(rolling_latency[degrade_idx]), 1),
    }


# ── statistics helpers (pure numpy; replace scipy.stats to keep the Lambda
# deployment package under the 250 MB unzipped limit) ───────────────────────
def _skewness(values: np.ndarray) -> float:
    """Fisher-Pearson skewness (biased), matching scipy.stats.skew defaults."""
    arr = np.asarray(values, dtype=float)
    mean = arr.mean()
    diffs = arr - mean
    m2 = np.mean(diffs ** 2)
    if m2 == 0:
        return 0.0
    m3 = np.mean(diffs ** 3)
    return float(m3 / (m2 ** 1.5))


def _kurtosis_excess(values: np.ndarray) -> float:
    """Excess kurtosis (Fisher, biased), matching scipy.stats.kurtosis defaults."""
    arr = np.asarray(values, dtype=float)
    mean = arr.mean()
    diffs = arr - mean
    m2 = np.mean(diffs ** 2)
    if m2 == 0:
        return 0.0
    m4 = np.mean(diffs ** 4)
    return float(m4 / (m2 ** 2) - 3.0)
