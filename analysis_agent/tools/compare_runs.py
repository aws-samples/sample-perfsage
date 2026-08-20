import json

import boto3
import numpy as np
from strands import tool


@tool
def compare_runs(current_metrics_json: str, baseline_s3_bucket: str, baseline_s3_key: str) -> str:
    """Compare current test results against a previous baseline run.

    Pulls baseline metrics from S3 and computes per-metric deltas, identifying improvements,
    degradations, and unchanged metrics. Helps track performance trends across releases.
    """
    try:
        current = json.loads(current_metrics_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid current metrics JSON: {str(e)}"})

    s3 = boto3.client("s3")
    try:
        baseline_obj = s3.get_object(Bucket=baseline_s3_bucket, Key=baseline_s3_key)
        baseline = json.loads(baseline_obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        return json.dumps({"error": f"Baseline file not found: s3://{baseline_s3_bucket}/{baseline_s3_key}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to load baseline: {str(e)}"})

    comparisons = []

    metric_paths = [
        ("latency.p50_ms", "P50 Latency (ms)", "lower_is_better"),
        ("latency.p90_ms", "P90 Latency (ms)", "lower_is_better"),
        ("latency.p99_ms", "P99 Latency (ms)", "lower_is_better"),
        ("latency.mean_ms", "Mean Latency (ms)", "lower_is_better"),
        ("rps_mean", "Mean RPS", "higher_is_better"),
        ("rps_max", "Max RPS", "higher_is_better"),
        ("error_rate", "Error Rate", "lower_is_better"),
    ]

    for path, display_name, preference in metric_paths:
        current_val = _resolve(current, path)
        baseline_val = _resolve(baseline, path)

        if current_val is None or baseline_val is None:
            continue

        if baseline_val == 0:
            change_percent = 100.0 if current_val > 0 else 0.0
        else:
            change_percent = ((current_val - baseline_val) / baseline_val) * 100

        if abs(change_percent) < 2:
            direction = "unchanged"
        elif preference == "lower_is_better":
            direction = "improved" if change_percent < 0 else "degraded"
        else:
            direction = "improved" if change_percent > 0 else "degraded"

        comparisons.append({
            "metric": display_name,
            "current_value": round(current_val, 3),
            "baseline_value": round(baseline_val, 3),
            "change_percent": round(change_percent, 1),
            "direction": direction,
        })

    improvements = [c for c in comparisons if c["direction"] == "improved"]
    degradations = [c for c in comparisons if c["direction"] == "degraded"]

    overall = "improved"
    if len(degradations) > len(improvements):
        overall = "degraded"
    elif len(degradations) == 0 and len(improvements) == 0:
        overall = "unchanged"

    current_latencies = current.get("latency_over_time", [])
    baseline_latencies = baseline.get("latency_over_time", [])
    distribution_shift = _test_distribution_shift(current_latencies, baseline_latencies)

    return json.dumps({
        "comparisons": comparisons,
        "overall_trend": overall,
        "improvements_count": len(improvements),
        "degradations_count": len(degradations),
        "distribution_shift": distribution_shift,
    })


def _resolve(data: dict, path: str):
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


def _test_distribution_shift(current: list, baseline: list) -> dict:
    if not current or not baseline:
        return {"tested": False}

    current_arr = np.array(current[:1000])
    baseline_arr = np.array(baseline[:1000])

    statistic, p_value = _ks_2samp(current_arr, baseline_arr)

    return {
        "tested": True,
        "ks_statistic": round(float(statistic), 4),
        "p_value": round(float(p_value), 6),
        "significant_shift": p_value < 0.05,
    }


def _ks_2samp(data1: np.ndarray, data2: np.ndarray):
    """Two-sample Kolmogorov-Smirnov test in pure numpy.

    Returns (statistic, p_value). Replaces scipy.stats.ks_2samp so the Lambda
    package stays under the 250 MB unzipped limit. The statistic is exact; the
    p-value uses the asymptotic Kolmogorov distribution (Numerical Recipes
    form), which is accurate for the sample sizes used here (up to 1000).
    """
    d1 = np.sort(np.asarray(data1, dtype=float))
    d2 = np.sort(np.asarray(data2, dtype=float))
    n1 = d1.size
    n2 = d2.size

    if n1 == 0 or n2 == 0:
        return 0.0, 1.0

    data_all = np.concatenate([d1, d2])
    cdf1 = np.searchsorted(d1, data_all, side="right") / n1
    cdf2 = np.searchsorted(d2, data_all, side="right") / n2
    statistic = float(np.max(np.abs(cdf1 - cdf2)))

    en = np.sqrt(n1 * n2 / (n1 + n2))
    lam = (en + 0.12 + 0.11 / en) * statistic
    p_value = _kolmogorov_q(lam)
    return statistic, p_value


def _kolmogorov_q(lam: float) -> float:
    """Q_KS(lambda) = 2 * sum_{j>=1} (-1)^(j-1) exp(-2 j^2 lambda^2)."""
    if lam <= 0:
        return 1.0
    total = 0.0
    for j in range(1, 101):
        term = 2.0 * ((-1) ** (j - 1)) * np.exp(-2.0 * (j ** 2) * (lam ** 2))
        total += term
        if abs(term) < 1e-10:
            break
    return float(min(1.0, max(0.0, total)))
