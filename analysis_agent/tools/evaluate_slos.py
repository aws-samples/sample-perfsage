import json

from strands import tool


OPERATOR_MAP = {
    "lt": lambda actual, threshold: actual < threshold,
    "lte": lambda actual, threshold: actual <= threshold,
    "gt": lambda actual, threshold: actual > threshold,
    "gte": lambda actual, threshold: actual >= threshold,
}


@tool
def evaluate_slos(
    metrics_json: str,
    slo_definitions_json: str,
    xray_json: str = "",
    cloudwatch_json: str = "",
) -> str:
    """Evaluate test results against user-defined SLO (Service Level Objective) thresholds.

    Checks each SLO definition against the actual metrics and returns a pass/fail verdict
    for each SLO plus an overall verdict. Common SLOs: p99 latency < 500ms, error rate < 1%.

    Client-side metric paths (from ingest_results): ``latency.p50_ms``/``p90_ms``/
    ``p95_ms``/``p99_ms``/``mean_ms``/``max_ms``, ``error_rate``, ``rps_mean``,
    ``rps_max``, ``total_requests``, ``failed_requests``, ``duration_seconds``,
    ``vus_max``.

    Server-side metric paths — available when the corresponding tool output is
    passed in:
    * From ``xray_json`` (fetch_xray_traces output): ``xray.cold_starts``,
      ``xray.cold_start_rate`` (0..1), ``xray.avg_init_ms``, ``xray.max_init_ms``,
      ``xray.faults``, ``xray.errors``, ``xray.throttles``, ``xray.p99_ms``.
    * From ``cloudwatch_json`` (fetch_cloudwatch_metrics output):
      ``cloudwatch.lambda_throttles``, ``cloudwatch.lambda_errors``,
      ``cloudwatch.lambda_concurrency_max``, ``cloudwatch.lambda_duration_avg``,
      ``cloudwatch.api_5xx``, ``cloudwatch.api_4xx``,
      ``cloudwatch.api_integration_latency_avg``, ``cloudwatch.dynamodb_throttled``.

    Always pass ``xray_json`` and ``cloudwatch_json`` when those tools returned data,
    so SLOs on cold-start rate / throttles / 5XX can be evaluated.
    """
    try:
        metrics = json.loads(metrics_json)
        slo_definitions = json.loads(slo_definitions_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON input: {str(e)}"})

    return json.dumps(
        evaluate_slos_core(
            metrics, slo_definitions, _try_load(xray_json), _try_load(cloudwatch_json)
        )
    )


def evaluate_slos_core(
    metrics: dict,
    slo_definitions: list,
    xray: dict | None = None,
    cloudwatch: dict | None = None,
) -> dict:
    """Deterministic SLO evaluation on already-parsed dicts.

    Shared by the ``evaluate_slos`` @tool (agent) and the Lambda handler. Merges
    X-Ray / CloudWatch scalars under ``xray.*`` / ``cloudwatch.*`` so SLOs can
    target server-side signals. Each result carries an ``evaluated`` flag: rows
    whose metric can't be resolved (or whose definition is invalid) are marked
    ``evaluated: False`` and are EXCLUDED from the overall verdict rather than
    silently failing the run.
    """
    merged = dict(metrics or {})
    _augment_with_server_side(merged, xray, cloudwatch)

    results = []
    for slo_def in slo_definitions or []:
        try:
            name = slo_def["name"]
            metric_path = slo_def["metric"]
            threshold = float(slo_def["threshold"])
            operator = slo_def["operator"]
        except (KeyError, TypeError, ValueError) as e:
            results.append({
                "name": (slo_def or {}).get("name", "unknown") if isinstance(slo_def, dict) else "unknown",
                "passed": False,
                "evaluated": False,
                "reason": f"Invalid SLO definition: {str(e)}",
            })
            continue

        actual_value = _resolve_metric(merged, metric_path)
        if actual_value is None:
            results.append({
                "name": name,
                "metric": metric_path,
                "threshold": threshold,
                "operator": operator,
                "actual_value": None,
                "passed": False,
                "evaluated": False,
                "reason": f"Metric '{metric_path}' not available in collected data",
            })
            continue

        check_fn = OPERATOR_MAP.get(operator)
        if not check_fn:
            results.append({
                "name": name,
                "metric": metric_path,
                "threshold": threshold,
                "operator": operator,
                "actual_value": round(actual_value, 3),
                "passed": False,
                "evaluated": False,
                "reason": f"Unknown operator '{operator}'",
            })
            continue

        results.append({
            "name": name,
            "metric": metric_path,
            "threshold": threshold,
            "operator": operator,
            "actual_value": round(actual_value, 3),
            "passed": check_fn(actual_value, threshold),
            "evaluated": True,
        })

    # Overall verdict is computed only over rows we could actually evaluate.
    evaluated = [r for r in results if r.get("evaluated")]
    failed = [r for r in evaluated if not r["passed"]]
    if not evaluated:
        overall = "unknown"
    elif not failed:
        overall = "pass"
    else:
        overall = "fail" if len(failed) > len(evaluated) / 2 else "warning"

    return {
        "slo_results": results,
        "overall_verdict": overall,
        "passed_count": sum(1 for r in evaluated if r["passed"]),
        "failed_count": len(failed),
        "evaluated_count": len(evaluated),
        "total_slos": len(results),
    }


def _resolve_metric(metrics: dict, path: str):
    parts = path.split(".")
    current = metrics
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


def _augment_with_server_side(metrics: dict, xray, cloudwatch) -> None:
    """Merge X-Ray / CloudWatch scalars into `metrics` under xray.* / cloudwatch.*
    so SLOs can target server-side signals (cold starts, throttles, 5XX).

    Accepts already-parsed dicts (handler path) or None. Strings are tolerated
    for backwards compatibility."""
    xray = _try_load(xray) if isinstance(xray, str) else xray
    if isinstance(xray, dict) and xray.get("available"):
        cold = xray.get("cold_starts") or {}
        trace_count = _num(xray.get("trace_count")) or 0
        cold_count = _num(cold.get("count")) or 0
        rt = xray.get("response_time_ms") or {}
        metrics["xray"] = {
            "cold_starts": cold_count,
            "cold_start_rate": (cold_count / trace_count) if trace_count else 0.0,
            "avg_init_ms": _num(cold.get("avg_init_ms")) or 0.0,
            "max_init_ms": _num(cold.get("max_init_ms")) or 0.0,
            "faults": _num(xray.get("faults")) or 0,
            "errors": _num(xray.get("errors")) or 0,
            "throttles": _num(xray.get("throttles")) or 0,
            "p99_ms": _num(rt.get("p99")) or 0.0,
            "trace_count": trace_count,
        }

    cw = _try_load(cloudwatch) if isinstance(cloudwatch, str) else cloudwatch
    if isinstance(cw, dict) and cw.get("available"):
        m = cw.get("metrics") or {}

        def agg(label: str, field: str):
            return _num((m.get(label) or {}).get(field))

        metrics["cloudwatch"] = {
            "lambda_throttles": agg("Lambda.Throttles.Sum", "sum") or 0,
            "lambda_errors": agg("Lambda.Errors.Sum", "sum") or 0,
            "lambda_invocations": agg("Lambda.Invocations.Sum", "sum") or 0,
            "lambda_concurrency_max": agg("Lambda.ConcurrentExecutions.Maximum", "max") or 0,
            "lambda_duration_avg": agg("Lambda.Duration.Average", "avg") or 0.0,
            "api_5xx": agg("ApiGateway.5XXError.Sum", "sum") or 0,
            "api_4xx": agg("ApiGateway.4XXError.Sum", "sum") or 0,
            "api_integration_latency_avg": agg("ApiGateway.IntegrationLatency.Average", "avg") or 0.0,
            "dynamodb_throttled": agg("DynamoDB.ThrottledRequests.Sum", "sum") or 0,
        }


def _try_load(raw):
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
