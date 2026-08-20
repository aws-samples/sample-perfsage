"""AWS Lambda handler for the PerfSage Analysis Agent.

Invoked after the Executor Agent completes a test run. Returns the analysis
report.

Supports both invocation styles:
* Direct invoke / console test — payload fields at the top level of ``event``.
* API Gateway / proxy (and the frontend's Lambda proxy) — payload is a JSON
  string in ``event["body"]``.

NOTE: Do NOT create a Lambda Function URL — use API Gateway or console test only
(per team policy).
"""

import json
import logging
import os

import boto3

from analysis_agent.agent import create_analysis_agent
from analysis_agent.tools.ingest_results import ingest_metrics
from analysis_agent.tools.fetch_xray_traces import summarize_xray_traces
from analysis_agent.tools.fetch_cloudwatch_metrics import summarize_cloudwatch_metrics
from analysis_agent.tools.evaluate_slos import evaluate_slos_core

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3_BUCKET = os.environ["PERFSAGE_S3_BUCKET"]
DYNAMODB_TABLE = os.environ["PERFSAGE_DYNAMODB_TABLE"]


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_summary(test_run_id: str) -> dict | None:
    """Read the run's k6 summary-export from S3 and shape it for the frontend.

    Returns a TestSummary-shaped dict (camelCase, errorRate as a 0..1 fraction)
    or None if summary.json is missing/unreadable.
    """
    key = f"runs/{test_run_id}/summary.json"
    try:
        obj = boto3.client("s3").get_object(Bucket=S3_BUCKET, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.info("summary.json unavailable for %s: %s", test_run_id, e)
        return None

    metrics = data.get("metrics", {}) or {}
    duration = metrics.get("http_req_duration", {}) or {}
    failed = metrics.get("http_req_failed", {}) or {}
    reqs = metrics.get("http_reqs", {}) or {}
    vus = metrics.get("vus", {}) or {}

    return {
        "totalRequests": int(_f(reqs.get("count"))),
        "errorRate": _f(failed.get("value")),  # fraction 0..1
        "avgLatency": _f(duration.get("avg")),
        "p50Latency": _f(duration.get("med")),
        "p90Latency": _f(duration.get("p(90)")),
        "p95Latency": _f(duration.get("p(95)")),
        "p99Latency": _f(duration.get("p(99)")) or _f(duration.get("max")),
        "rps": _f(reqs.get("rate")),
        "duration": "",
        "vus": int(_f(vus.get("max"))),
    }


def _extract_host(target_url) -> str:
    """Pull the API Gateway id / host substring from a target URL for X-Ray filtering."""
    if not target_url or not isinstance(target_url, str):
        return ""
    from urllib.parse import urlparse
    parsed = urlparse(target_url if "://" in target_url else "https://" + target_url)
    host = parsed.netloc or parsed.path
    return host.split(".")[0] if host else ""


def _lambda_name_from_uri(uri: str) -> str:
    """Extract the Lambda function name from an API Gateway integration URI."""
    marker = ":function:"
    if marker in (uri or ""):
        after = uri.split(marker, 1)[1]
        return after.split("/")[0].split(":")[0]  # NAME (strip alias + /invocations)
    return ""


def _derive_resources(target_url) -> dict:
    """Best-effort: derive CloudWatch resource names from the target API URL.

    Lets CloudWatch grounding work from the UI, which only passes target_url.
    Resolves the API Gateway name (for the ApiName dimension) and the backing
    Lambda function name (from an integration URI). Any failure is swallowed so
    analysis never breaks — it just falls back to X-Ray + client-side metrics.
    """
    out = {}
    api_id = _extract_host(target_url)
    if not api_id:
        return out
    try:
        apigw = boto3.client("apigateway")
        api = apigw.get_rest_api(restApiId=api_id)
        if api.get("name"):
            out["api_gateway_name"] = api["name"]
        resources = apigw.get_resources(restApiId=api_id, limit=200).get("items", [])
        for res in resources:
            for http_method in (res.get("resourceMethods") or {}):
                try:
                    integ = apigw.get_integration(
                        restApiId=api_id, resourceId=res["id"], httpMethod=http_method
                    )
                except Exception:  # noqa: BLE001
                    continue
                fn = _lambda_name_from_uri(integ.get("uri", ""))
                if fn:
                    out["lambda_function_name"] = fn
                    return out
    except Exception as e:  # noqa: BLE001
        logger.info("Resource derivation failed for api '%s': %s", api_id, e)
    return out


def _extract_params(event) -> dict:
    """Normalize the request params across direct and API-Gateway/proxy events."""
    if not isinstance(event, dict):
        return {}
    # API Gateway / proxy integration: payload is a JSON string in "body".
    body = event.get("body")
    if body:
        if isinstance(body, str):
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {}
        elif isinstance(body, dict):
            return body
    # Direct invoke / console test: fields are at the top level.
    return event


def _response(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(payload),
    }


def _evaluate_slos_deterministic(
    test_run_id: str,
    s3_key: str,
    slo_definitions,
    xray_host: str,
    api_gateway_name,
    lambda_function_name,
    dynamodb_table_name,
) -> dict | None:
    """Deterministically evaluate SLOs server-side, independent of the LLM.

    Ingests the run's metrics, pulls X-Ray + CloudWatch scalars for the derived
    target resources, and evaluates the SLO definitions. Returns the structured
    result (``slo_results`` + ``overall_verdict``) or None if no SLOs / on error.
    This is the authoritative SLO evaluation the frontend renders — the agent's
    own evaluate_slos call is only for its narrative.
    """
    if not slo_definitions:
        return None
    try:
        metrics = ingest_metrics(S3_BUCKET, s3_key, DYNAMODB_TABLE, test_run_id)
        if not isinstance(metrics, dict) or metrics.get("error"):
            metrics = {}

        xray = summarize_xray_traces(test_run_id, xray_host) if xray_host else None

        cw = None
        if any([api_gateway_name, lambda_function_name, dynamodb_table_name]):
            cw = summarize_cloudwatch_metrics(
                test_run_id,
                api_gateway_name or "",
                lambda_function_name or "",
                dynamodb_table_name or "",
            )

        result = evaluate_slos_core(metrics, slo_definitions, xray, cw)
        logger.info("Deterministic SLO verdict for %s: %s (%s evaluated)",
                    test_run_id, result.get("overall_verdict"),
                    result.get("evaluated_count"))
        return result
    except Exception:  # noqa: BLE001 - SLO eval must never break the main response
        logger.exception("Deterministic SLO evaluation failed for %s", test_run_id)
        return None


def handler(event, context):
    params = _extract_params(event)

    test_run_id = params.get("test_run_id")
    if not test_run_id:
        logger.error("Missing test_run_id in event: %s", json.dumps(event)[:500])
        return _response(400, {"error": "test_run_id is required"})

    logger.info("Analysis Agent invoked for test run: %s", test_run_id)

    s3_key = params.get("s3_key", f"runs/{test_run_id}/metrics.json")
    slo_definitions = params.get("slo_definitions")
    baseline_s3_key = params.get("baseline_s3_key")
    target_url = params.get("target_url") or params.get("filter_host")

    prompt_parts = [
        f"Analyze load test results for run '{test_run_id}'.",
        f"Raw metrics are in s3://{S3_BUCKET}/{s3_key}.",
        f"Test summary is in DynamoDB table '{DYNAMODB_TABLE}' with key '{test_run_id}'.",
    ]

    xray_host = _extract_host(target_url)
    prompt_parts.append(
        f"Call fetch_xray_traces with test_run_id '{test_run_id}'"
        + (f" and filter_host '{xray_host}'" if xray_host else "")
        + " to obtain server-side trace evidence (cold-start init times, "
          "per-segment latency, faults/throttles). If traces are returned, base "
          "the root cause and recommendations on that evidence."
    )

    # Target resource hints for CloudWatch metric attribution. If not explicitly
    # provided, derive the API Gateway + Lambda names from the target URL so
    # CloudWatch grounding works from the UI (which only passes target_url).
    api_gateway_name = params.get("api_gateway_name")
    lambda_function_name = params.get("lambda_function_name")
    dynamodb_table_name = params.get("dynamodb_table_name")
    if target_url and not (api_gateway_name and lambda_function_name):
        derived = _derive_resources(target_url)
        api_gateway_name = api_gateway_name or derived.get("api_gateway_name")
        lambda_function_name = lambda_function_name or derived.get("lambda_function_name")
        logger.info("Derived CloudWatch resources: apigw=%s lambda=%s",
                    api_gateway_name, lambda_function_name)
    if any([api_gateway_name, lambda_function_name, dynamodb_table_name]):
        cw_args = ", ".join(
            f"{k}='{v}'" for k, v in [
                ("api_gateway_name", api_gateway_name),
                ("lambda_function_name", lambda_function_name),
                ("dynamodb_table_name", dynamodb_table_name),
            ] if v
        )
        prompt_parts.append(
            f"Also call fetch_cloudwatch_metrics with test_run_id '{test_run_id}' and "
            f"{cw_args} to confirm throttling/capacity/error root causes with metric values."
        )

    if slo_definitions:
        prompt_parts.append(
            f"Evaluate against these SLOs: {json.dumps(slo_definitions)}"
        )

    if baseline_s3_key:
        prompt_parts.append(
            f"Compare against baseline run at s3://{S3_BUCKET}/{baseline_s3_key}."
        )

    prompt = " ".join(prompt_parts)

    try:
        agent = create_analysis_agent()
        response = agent(prompt)
        report = str(response)
    except Exception as exc:  # noqa: BLE001 - return a clean error to the caller
        logger.exception("Analysis failed for run %s", test_run_id)
        return _response(500, {"test_run_id": test_run_id, "error": str(exc)})

    logger.info("Analysis complete for run: %s", test_run_id)

    # Authoritative, deterministic SLO evaluation (independent of the LLM) so the
    # UI banner/table reflect the exact same numbers every run.
    slo_eval = _evaluate_slos_deterministic(
        test_run_id, s3_key, slo_definitions, xray_host,
        api_gateway_name, lambda_function_name, dynamodb_table_name,
    )

    payload = {
        "test_run_id": test_run_id,
        "analysis_report": report,
        "summary": _build_summary(test_run_id),
    }
    if slo_eval:
        payload["slo_results"] = slo_eval.get("slo_results", [])
        payload["slo_verdict"] = slo_eval.get("overall_verdict")

    return _response(200, payload)
