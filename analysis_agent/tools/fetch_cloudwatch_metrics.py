"""Fetch AWS CloudWatch metrics for a load-test run's target resources.

Complements fetch_xray_traces: where X-Ray attributes *latency* to a component,
CloudWatch metrics attribute *throttling and errors* to a specific AWS service
(Lambda throttles/concurrency, API Gateway integration latency + 5xx, DynamoDB
throttled/consumed capacity). This is the strongest signal for "rate limiting"
or "capacity" root causes.

The tool derives the test window from the run row in DynamoDB and pulls metrics
for whatever target resources it can identify from the provided hints.
"""
import datetime as dt
import json
import os

import boto3
from strands import tool

DYNAMODB_TABLE = os.environ.get("PERFSAGE_DYNAMODB_TABLE", "perfsage-test-runs")
REGION = os.environ.get("AWS_REGION", "us-east-1")


@tool
def fetch_cloudwatch_metrics(
    test_run_id: str,
    api_gateway_name: str = "",
    lambda_function_name: str = "",
    dynamodb_table_name: str = "",
) -> str:
    """Fetch CloudWatch metrics for the target app over the test window.

    Use this to confirm capacity/throttling root causes with hard numbers:
    - API Gateway: Count, Latency, IntegrationLatency, 4XXError, 5XXError
    - Lambda: Invocations, Errors, Throttles, ConcurrentExecutions, Duration
    - DynamoDB: ThrottledRequests, ConsumedRead/WriteCapacity, SystemErrors

    Args:
        test_run_id: Run id (used to look up the test window in DynamoDB).
        api_gateway_name: Target API Gateway REST API name (e.g. the ApiName
            dimension) to pull API GW metrics. Optional.
        lambda_function_name: Target Lambda function name, if the app is Lambda-backed.
        dynamodb_table_name: Target DynamoDB table name, if the app uses DynamoDB.

    Returns a JSON summary of the metrics. Non-zero Throttles / ThrottledRequests
    / 5XXError are strong, direct evidence — cite them with their values.
    """
    return json.dumps(summarize_cloudwatch_metrics(
        test_run_id, api_gateway_name, lambda_function_name, dynamodb_table_name
    ))


def summarize_cloudwatch_metrics(
    test_run_id: str,
    api_gateway_name: str = "",
    lambda_function_name: str = "",
    dynamodb_table_name: str = "",
) -> dict:
    """Core CloudWatch summary logic returning a dict.

    Shared by the ``fetch_cloudwatch_metrics`` @tool (agent) and the Lambda
    handler (deterministic SLO evaluation)."""
    cw = boto3.client("cloudwatch", region_name=REGION)
    start_dt, end_dt, window_source = _resolve_window(test_run_id)

    queries = []
    labels = {}

    def add(mid, namespace, metric, dims, stat):
        queries.append({
            "Id": mid,
            "MetricStat": {
                "Metric": {"Namespace": namespace, "MetricName": metric,
                           "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]},
                "Period": 60,
                "Stat": stat,
            },
            "ReturnData": True,
        })
        labels[mid] = f"{namespace.split('/')[-1]}.{metric}.{stat}"

    if api_gateway_name:
        d = {"ApiName": api_gateway_name}
        add("api_count", "AWS/ApiGateway", "Count", d, "Sum")
        add("api_latency", "AWS/ApiGateway", "Latency", d, "Average")
        add("api_integlatency", "AWS/ApiGateway", "IntegrationLatency", d, "Average")
        add("api_4xx", "AWS/ApiGateway", "4XXError", d, "Sum")
        add("api_5xx", "AWS/ApiGateway", "5XXError", d, "Sum")

    if lambda_function_name:
        d = {"FunctionName": lambda_function_name}
        add("fn_invocations", "AWS/Lambda", "Invocations", d, "Sum")
        add("fn_errors", "AWS/Lambda", "Errors", d, "Sum")
        add("fn_throttles", "AWS/Lambda", "Throttles", d, "Sum")
        add("fn_concurrency", "AWS/Lambda", "ConcurrentExecutions", d, "Maximum")
        add("fn_duration", "AWS/Lambda", "Duration", d, "Average")

    if dynamodb_table_name:
        d = {"TableName": dynamodb_table_name}
        add("ddb_throttled", "AWS/DynamoDB", "ThrottledRequests", d, "Sum")
        add("ddb_read", "AWS/DynamoDB", "ConsumedReadCapacityUnits", d, "Sum")
        add("ddb_write", "AWS/DynamoDB", "ConsumedWriteCapacityUnits", d, "Sum")
        add("ddb_syserr", "AWS/DynamoDB", "SystemErrors", d, "Sum")

    if not queries:
        return {
            "available": False,
            "message": (
                "No target resource identifiers provided (api_gateway_name / "
                "lambda_function_name / dynamodb_table_name). Cannot query "
                "CloudWatch. Provide at least one to attribute throttling/errors."
            ),
        }

    metrics_out = {}
    try:
        results = _get_metric_data(cw, queries, start_dt, end_dt)
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e),
                "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat()}}

    for r in results:
        vals = r.get("Values", [])
        label = labels.get(r["Id"], r["Id"])
        stat = label.rsplit(".", 1)[-1]
        if not vals:
            metrics_out[label] = {"datapoints": 0}
            continue
        # For Sum stats report total; for Average/Maximum report peak + avg.
        agg = {
            "datapoints": len(vals),
            "sum": round(sum(vals), 2),
            "max": round(max(vals), 2),
            "avg": round(sum(vals) / len(vals), 2),
        }
        metrics_out[label] = agg

    # Pull out the headline red-flag signals for easy consumption
    flags = []
    for key, agg in metrics_out.items():
        if key.endswith(".Sum") and ("Throttle" in key or "5XXError" in key or "SystemErrors" in key):
            if agg.get("sum", 0) > 0:
                flags.append(f"{key} = {agg['sum']}")

    return {
        "available": True,
        "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat(),
                   "source": window_source},
        "targets": {
            "api_gateway_name": api_gateway_name or None,
            "lambda_function_name": lambda_function_name or None,
            "dynamodb_table_name": dynamodb_table_name or None,
        },
        "metrics": metrics_out,
        "red_flags": flags or ["none - no throttling/5xx/system errors observed"],
        "interpretation_hint": (
            "Non-zero Throttles/ThrottledRequests confirm a rate-limiting or "
            "under-provisioned-capacity root cause. High IntegrationLatency with "
            "low API Gateway Latency points at the backend, not the gateway. "
            "5XXError/SystemErrors confirm server-side failures."
        ),
    }


# ── helpers ─────────────────────────────────────────────────────────────────
def _resolve_window(test_run_id: str):
    try:
        table = boto3.resource("dynamodb", region_name=REGION).Table(DYNAMODB_TABLE)
        item = table.get_item(Key={"test_id": test_run_id}).get("Item", {})
        started = _epoch_s(item.get("started_at"))
        ended = _epoch_s(item.get("ended_at"))
        if started and ended and ended > started:
            return (
                dt.datetime.fromtimestamp(started - 60, dt.timezone.utc),
                dt.datetime.fromtimestamp(ended + 60, dt.timezone.utc),
                "dynamodb(started_at/ended_at)",
            )
    except Exception:  # noqa: BLE001
        pass
    now = dt.datetime.now(dt.timezone.utc)
    return now - dt.timedelta(hours=2), now, "fallback(last 2h)"


def _epoch_s(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n / 1000.0 if n > 1e12 else n


def _get_metric_data(cw, queries, start_dt, end_dt):
    results = []
    kwargs = {"MetricDataQueries": queries, "StartTime": start_dt, "EndTime": end_dt,
              "ScanBy": "TimestampAscending"}
    token = None
    for _ in range(10):
        if token:
            kwargs["NextToken"] = token
        resp = cw.get_metric_data(**kwargs)
        results.extend(resp.get("MetricDataResults", []))
        token = resp.get("NextToken")
        if not token:
            break
    return results
