"""AWS Lambda handler for the PerfSage Executor Agent.

Thin wrapper that receives a k6 script + test parameters from the frontend,
kicks off the Strands Agent asynchronously, and returns a test_id for polling.

Supports two invocation modes:
- Synchronous (direct invoke): runs the full agent and returns results (up to 15min)
- Async kickoff: writes PENDING to DynamoDB, self-invokes async, returns test_id immediately

Lambda event format:
{
    "body": "{\"script\": \"...\", \"vus\": 100, \"targetUrl\": \"https://...\"}"
}

Environment variables:
- PERFSAGE_S3_BUCKET: S3 bucket for results
- PERFSAGE_DYNAMODB_TABLE: DynamoDB table for test runs
- PERFSAGE_EXECUTION_MODE: 'fargate' (default)
- PERFSAGE_MODEL_ID: Bedrock model for the agent
- AWS_REGION: us-east-1
"""

import json
import logging
import os
import traceback
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def handler(event, context):
    """Lambda entry point."""
    # Check if this is an async worker invocation
    if event.get("source") == "executor_worker":
        return _handle_worker(event)

    # Route based on HTTP method and path
    method = event.get("httpMethod", "POST")
    path = event.get("path", "/")

    # GET /executor/status/{id} — poll test status
    if method == "GET" and "/status/" in path:
        test_id = path.split("/status/")[-1].strip("/")
        return _handle_status(test_id)

    # POST /executor/run — start a test
    body = _parse_body(event)
    if not body:
        return _response(400, {"error": "Missing request body"})
    if "error" in body:
        return _response(400, body)

    script = body.get("script", "")
    vus = body.get("vus", 10)
    duration = body.get("duration", "")
    target_url = body.get("targetUrl", "")

    if not script:
        return _response(400, {"error": "Missing 'script' field — provide the k6 JavaScript content"})

    # Generate test ID
    test_id = f"run-{uuid.uuid4().hex[:12]}"

    # Write initial status to DynamoDB
    _write_pending_status(test_id, vus, duration)

    # Self-invoke asynchronously for the actual execution
    lambda_client = boto3.client("lambda")
    lambda_client.invoke(
        FunctionName=context.function_name,
        InvocationType="Event",
        Payload=json.dumps({
            "source": "executor_worker",
            "test_id": test_id,
            "script": script,
            "vus": vus,
            "duration": duration,
            "target_url": target_url,
            "target_rps": body.get("targetRps", 40),
        }),
    )

    # Return immediately with test_id for polling
    return _response(202, {
        "test_id": test_id,
        "status": "provisioning",
        "message": "Test submitted. Poll GET /status/{test_id} for updates.",
    })


def _handle_worker(event):
    """Async worker — runs the deterministic orchestrator to execute the test.

    Includes idempotency guard: if this test_id already has a running/completed status,
    skip re-execution. This prevents Lambda's automatic retry on timeout from creating
    duplicate Fargate tasks.
    """
    test_id = event["test_id"]
    script = event["script"]
    vus = event.get("vus", 10)
    duration = event.get("duration", "")
    target_url = event.get("target_url", "")
    target_rps = event.get("target_rps", 40)

    logger.info(f"Executor worker started for test {test_id} (target_rps={target_rps})")

    # Idempotency guard: if test is already running or completed, don't re-execute.
    # This prevents Lambda retry (on timeout) from spawning duplicate Fargate tasks.
    try:
        table_name = os.environ.get("PERFSAGE_DYNAMODB_TABLE", "perfsage-test-runs")
        dynamodb_check = boto3.resource("dynamodb")
        table_check = dynamodb_check.Table(table_name)
        existing = table_check.get_item(Key={"test_id": test_id}).get("Item", {})
        existing_status = existing.get("status", "")

        if existing_status in ("running", "completed", "aborted"):
            logger.info(f"Idempotency guard: test {test_id} already in '{existing_status}' state, skipping re-execution")
            return {"status": existing_status, "test_id": test_id, "skipped": True}
    except Exception as e:
        logger.warning(f"Idempotency check failed (proceeding anyway): {e}")

    try:
        # Write script to a temp file
        script_file = NamedTemporaryFile(mode="w", suffix=".js", delete=False)
        script_file.write(script)
        script_file.close()

        # Build TestConfig
        from perfsage_executor.models.test_config import TestConfig, ExecutionMode, TestScenarioParams

        scenario_params = None
        if target_url:
            scenario_params = TestScenarioParams(target_url=target_url)

        # Scale Fargate resources based on workload size
        fargate_cpu, fargate_memory = _get_fargate_sizing(vus, target_rps)

        # Estimate total_records from the script to calculate setupTimeout
        total_records = _estimate_records_from_script(script)

        config = TestConfig(
            test_id=test_id,
            script_path=script_file.name,
            virtual_users=vus,
            duration=duration,
            execution_mode=ExecutionMode.FARGATE,
            auto_stop_on_anomaly=True,
            scenario_params=scenario_params,
            fargate_cpu=fargate_cpu,
            fargate_memory=fargate_memory,
            total_records=total_records,
            environment_vars={
                "BATCH_SIZE": str(min(target_rps, 500)),
                "BATCH_SLEEP": "1",
                "TARGET_RPS": str(target_rps),
            },
        )

        # Mark as "running" in DynamoDB BEFORE starting Fargate.
        # This ensures the idempotency guard catches Lambda retries on timeout.
        try:
            table_name = os.environ.get("PERFSAGE_DYNAMODB_TABLE", "perfsage-test-runs")
            dynamodb_upd = boto3.resource("dynamodb")
            table_upd = dynamodb_upd.Table(table_name)
            table_upd.update_item(
                Key={"test_id": test_id},
                UpdateExpression="SET #s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "running"},
            )
        except Exception as e:
            logger.warning(f"Failed to update status to running: {e}")

        # Run the orchestrator
        from perfsage_executor.agent import run_test
        result = run_test(config.model_dump_json())

        logger.info(f"Executor completed for test {test_id}")

        # Clean up temp file
        os.unlink(script_file.name)

        return {"status": "completed", "test_id": test_id}

    except Exception as e:
        logger.error(f"Executor worker failed for {test_id}: {traceback.format_exc()}")
        _write_failed_status(test_id, str(e))

        # Clean up temp file if it exists
        try:
            os.unlink(script_file.name)
        except:
            pass

        return {"status": "failed", "test_id": test_id, "error": str(e)}


def _handle_status(test_id: str):
    """Poll DynamoDB for test run status. Self-heals from S3 if stuck."""
    try:
        table_name = os.environ.get("PERFSAGE_DYNAMODB_TABLE", "perfsage-test-runs")
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)
        response = table.get_item(Key={"test_id": test_id})
        item = response.get("Item")

        if not item:
            return _response(404, {"error": f"Test {test_id} not found"})

        status = item.get("status", "running")

        # Self-heal: if stuck at running/provisioning, check if S3 has results
        # (Fargate uploads results independently — the Worker Lambda may have died
        # before writing "completed" to DynamoDB)
        if status in ("running", "provisioning"):
            started_at = int(item.get("started_at", 0) or 0)
            elapsed_ms = int(time.time() * 1000) - started_at

            if elapsed_ms > 3 * 60 * 1000:  # After 3 min, start checking S3
                s3_summary = _try_read_s3_summary(test_id)
                if s3_summary:
                    logger.info(f"S3 reconciliation: marking {test_id} as completed")
                    table.update_item(
                        Key={"test_id": test_id},
                        UpdateExpression="SET #s = :s, summary = :sum, ended_at = :t, metrics_location = :ml",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={
                            ":s": "completed",
                            ":sum": s3_summary,
                            ":t": int(time.time() * 1000),
                            ":ml": f"s3://{os.environ.get('PERFSAGE_S3_BUCKET', '')}/runs/{test_id}/",
                        },
                    )
                    status = "completed"
                    item["summary"] = s3_summary

        result = {
            "test_id": item.get("test_id"),
            "status": status,
            "metrics_location": item.get("metrics_location"),
            "error_message": item.get("error_message"),
        }
        if "summary" in item:
            result["summary"] = item["summary"]

        return _response(200, result)
    except Exception as e:
        return _response(500, {"error": str(e)})


def _write_pending_status(test_id: str, vus: int, duration: str):
    """Write initial PENDING status to DynamoDB."""
    try:
        table_name = os.environ.get("PERFSAGE_DYNAMODB_TABLE", "perfsage-test-runs")
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)
        table.put_item(Item={
            "test_id": test_id,
            "status": "provisioning",
            "started_at": int(time.time() * 1000),
            "created_at": int(time.time() * 1000),
            "config_snapshot": {"vus": vus, "duration": duration},
            "expires_at": int(time.time()) + (90 * 24 * 3600),
        })
    except Exception as e:
        logger.error(f"Failed to write pending status: {e}")


def _write_failed_status(test_id: str, error: str):
    """Update DynamoDB with failed status."""
    try:
        table_name = os.environ.get("PERFSAGE_DYNAMODB_TABLE", "perfsage-test-runs")
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)
        table.update_item(
            Key={"test_id": test_id},
            UpdateExpression="SET #s = :s, error_message = :e, ended_at = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "failed",
                ":e": error,
                ":t": int(time.time() * 1000),
            },
        )
    except Exception as e:
        logger.error(f"Failed to write failed status: {e}")


def _try_read_s3_summary(test_id: str):
    """Check if Fargate uploaded summary.json to S3 (self-healing for stuck status).

    The Fargate container uploads results to S3 autonomously via entrypoint.sh.
    If the Worker Lambda died before writing 'completed' to DynamoDB, this function
    detects completion by checking S3 and returns the parsed summary.
    """
    try:
        from decimal import Decimal

        bucket = os.environ.get("PERFSAGE_S3_BUCKET")
        if not bucket:
            return None
        s3 = boto3.client("s3")
        obj = s3.get_object(Bucket=bucket, Key=f"runs/{test_id}/summary.json")
        data = json.loads(obj["Body"].read().decode("utf-8"))

        metrics = data.get("metrics", {})
        duration_m = metrics.get("http_req_duration", {})
        reqs = metrics.get("http_reqs", {})
        failed = metrics.get("http_req_failed", {})
        vus_m = metrics.get("vus", {}) or metrics.get("vus_max", {})

        # Handle both k6 summary formats (values nested or flat)
        def _val(metric_data, key):
            if isinstance(metric_data, dict):
                values = metric_data.get("values", metric_data)
                return float(values.get(key, 0) or 0)
            return 0.0

        # DynamoDB requires Decimal, not float
        def _dec(val):
            return Decimal(str(round(val, 2)))

        error_rate_raw = _val(failed, "value")
        error_rate_pct = error_rate_raw * 100 if error_rate_raw <= 1 else error_rate_raw

        return {
            "total_requests": int(_val(reqs, "count")),
            "error_rate_pct": _dec(error_rate_pct),
            "avg_latency_ms": _dec(_val(duration_m, "avg")),
            "p50_latency_ms": _dec(_val(duration_m, "med")),
            "p90_latency_ms": _dec(_val(duration_m, "p(90)")),
            "p95_latency_ms": _dec(_val(duration_m, "p(95)")),
            "p99_latency_ms": _dec(_val(duration_m, "p(99)") or _val(duration_m, "max")),
            "avg_rps": _dec(_val(reqs, "rate")),
            "peak_vus": int(_val(vus_m, "max")),
        }
    except Exception:
        return None


def _get_fargate_sizing(vus: int, target_rps: int = 40) -> tuple:
    """Determine Fargate CPU/memory based on workload size and target RPS.

    Higher RPS/VUs require more CPU for k6 to manage parallel connections
    and more memory for the metrics buffer. Tiers tuned to actual k6 usage
    (~1-5 MB per VU + metrics buffer). Returns (cpu_units, memory_mb) tuple.
    """
    if target_rps > 300 or vus > 200:
        return 2048, 8192   # high: 500 RPS + 100+ VUs needs real CPU for connection mgmt
    if target_rps > 100 or vus > 50:
        return 1024, 4096   # medium: 200 VUs ~1 GB peak, 1 vCPU handles 300 RPS batching
    return 512, 1024        # low: 10-50 VUs + batch-45 seeding is light


def _estimate_records_from_script(script: str) -> int | None:
    """Estimate total records to create from the k6 script content.

    Looks for patterns like:
      const NUM_COMPANIES = 50;
      const NUM_EMPLOYEES = 1000;
      const NUM_ADDRESSES = 8950;
    or PERFSAGE_TOTAL_RECORDS, iterations count, etc.

    Returns total record count or None if not determinable.
    """
    import re

    total = 0
    # Match const NUM_<anything> = <number>
    num_patterns = re.findall(r'(?:const|let|var)\s+NUM_\w+\s*=\s*(\d+)', script)
    if num_patterns:
        total = sum(int(n) for n in num_patterns)

    # Also check for explicit total_records or iterations
    if total == 0:
        iter_match = re.search(r'iterations\s*:\s*(\d+)', script)
        if iter_match:
            total = int(iter_match.group(1))

    # Check for loop patterns: for (let i = 0; i < 10000; i++)
    if total == 0:
        loop_counts = re.findall(r'for\s*\(\s*let\s+\w+\s*=\s*0\s*;\s*\w+\s*<\s*(\d+)', script)
        if loop_counts:
            total = sum(int(n) for n in loop_counts)

    return total if total > 0 else None


def _parse_body(event) -> dict:
    """Parse request body from API Gateway or direct invoke."""
    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        return body
    except json.JSONDecodeError:
        return {"error": "Invalid JSON body"}


def _response(status_code: int, body: dict) -> dict:
    """Format Lambda response for API Gateway."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }
