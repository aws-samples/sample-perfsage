"""Lambda handler for the TestGen agent.

Thin orchestrator pattern:
- API Gateway POST /jobs: uploads input to S3, launches Fargate task, returns job_id
- API Gateway GET /jobs/{id}: checks job status from DynamoDB
- Function URL POST: synchronous generation (for dev/test — runs in Lambda)
- Direct invocation (async worker): legacy Lambda self-invoke path (fallback)
"""
import json
import os
import uuid
import time
import logging
import traceback
from datetime import datetime, timezone

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")
ecs_client = boto3.client("ecs", config=Config(
    retries={"mode": "adaptive", "max_attempts": 3}
))
lambda_client = boto3.client("lambda", config=Config(
    retries={"mode": "adaptive", "max_attempts": 3}
))

STALE_JOB_TIMEOUT_SECONDS = 900
EXECUTION_MODE = os.environ.get("TESTGEN_EXECUTION_MODE", "fargate")


def lambda_handler(event, context):
    path = event.get("rawPath") or event.get("path") or "/"
    method = event.get("requestContext", {}).get("http", {}).get("method") or event.get("httpMethod", "POST")

    if path == "/health" or path.endswith("/health"):
        return _response(200, {"status": "healthy", "agent": "PerfSage-TestGen"})

    if event.get("source") == "worker":
        return _handle_worker(event)

    if "/jobs/" in path and method == "GET":
        return _handle_status(event)

    if path.endswith("/jobs") and method == "POST":
        return _handle_submit(event)

    if method == "POST":
        return _handle_sync(event)

    return _response(404, {"error": f"Not found: {method} {path}"})


def _handle_sync(event):
    """Synchronous generation — for Function URL / direct testing."""
    body = _parse_body(event)
    if "error" in body:
        return _response(400, body)

    spec_content = body.get("spec", "")
    user_request = body.get("prompt", "")
    spec_format = body.get("format", "yaml")
    dependencies = body.get("dependencies", None)
    records = body.get("records", None)
    context = body.get("context", None)

    if not spec_content or not user_request:
        return _response(400, {"error": "Both 'spec' and 'prompt' fields are required"})

    missing = []
    if dependencies is None:
        missing.append("dependencies")
    if records is None:
        missing.append("records")
    if context is None:
        missing.append("context")
    if missing:
        return _response(400, {
            "error": f"Missing required fields: {', '.join(missing)}",
            "hint": "Provide 'dependencies' (array, can be empty []), 'records' (object, e.g. {\"company\": 100}), and 'context' (string describing resources)"
        })

    try:
        from agent import generate_load_test
        result = generate_load_test(
            spec_content=spec_content,
            user_request=user_request,
            spec_format=spec_format,
            dependencies=dependencies,
            records=records,
            context=context,
        )
        return _response(200, result)
    except Exception as e:
        logger.error(f"Generation failed: {traceback.format_exc()}")
        return _response(500, {"error": str(e)})


def _handle_submit(event):
    """Submit a job — writes to DynamoDB, launches Fargate task or Lambda worker."""
    body = _parse_body(event)
    if "error" in body:
        return _response(400, body)

    job_id = str(uuid.uuid4())
    table_name = os.environ.get("JOB_TABLE_NAME")

    if table_name:
        table = dynamodb.Table(table_name)
        table.put_item(Item={
            "job_id": job_id,
            "status": "PENDING",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "request": body,
            "ttl": int(time.time()) + 86400,
        })

    if EXECUTION_MODE == "fargate":
        _launch_fargate_task(job_id, body)
    else:
        fn_name = os.environ.get("WORKER_FUNCTION_NAME", "")
        lambda_client.invoke(
            FunctionName=fn_name,
            InvocationType="Event",
            Payload=json.dumps({"source": "worker", "job_id": job_id, "request": body}),
        )

    return _response(202, {"job_id": job_id, "status": "PENDING"})


def _launch_fargate_task(job_id: str, body: dict):
    """Upload input to S3 and launch a Fargate task for generation."""
    try:
        bucket_name = os.environ.get("BUCKET_NAME", "")
        input_key = f"testgen-jobs/{job_id}/input.json"

        s3_client.put_object(
            Bucket=bucket_name,
            Key=input_key,
            Body=json.dumps(body, default=str).encode(),
            ContentType="application/json",
        )
        input_s3_uri = f"s3://{bucket_name}/{input_key}"

        cluster = os.environ.get("TESTGEN_ECS_CLUSTER", "perfsage-executor")
        task_def = os.environ.get("TESTGEN_TASK_DEF", "perfsage-testgen-runner")
        subnets = os.environ.get("TESTGEN_FARGATE_SUBNETS", "").split(",")
        security_groups = os.environ.get("TESTGEN_FARGATE_SECURITY_GROUPS", "").split(",")

        subnets = [s.strip() for s in subnets if s.strip()]
        security_groups = [s.strip() for s in security_groups if s.strip()]

        if not subnets or not security_groups:
            raise ValueError(
                "TESTGEN_FARGATE_SUBNETS and TESTGEN_FARGATE_SECURITY_GROUPS must be configured"
            )

        ecs_client.run_task(
            cluster=cluster,
            taskDefinition=task_def,
            launchType="FARGATE",
            count=1,
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": subnets,
                    "securityGroups": security_groups,
                    "assignPublicIp": "DISABLED",
                }
            },
            overrides={
                "containerOverrides": [{
                    "name": "testgen-runner",
                    "environment": [
                        {"name": "TESTGEN_JOB_ID", "value": job_id},
                        {"name": "TESTGEN_INPUT_S3_URI", "value": input_s3_uri},
                        {"name": "JOB_TABLE_NAME", "value": os.environ.get("JOB_TABLE_NAME", "")},
                        {"name": "BUCKET_NAME", "value": bucket_name},
                        {"name": "BEDROCK_MODEL_ID", "value": os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")},
                        {"name": "BEDROCK_REGION", "value": os.environ.get("BEDROCK_REGION", "us-east-1")},
                        {"name": "LOG_LEVEL", "value": os.environ.get("LOG_LEVEL", "INFO")},
                    ],
                }]
            },
        )
        logger.info(f"Launched Fargate task for job {job_id}")
    except Exception as e:
        logger.error(f"Failed to launch Fargate task for job {job_id}: {e}")
        raise


def _handle_status(event):
    """Check job status. Detects stale RUNNING jobs as FAILED."""
    path = event.get("rawPath") or event.get("path", "")
    job_id = path.split("/jobs/")[-1].strip("/")

    table_name = os.environ.get("JOB_TABLE_NAME")
    if not table_name:
        return _response(500, {"error": "JOB_TABLE_NAME not configured"})

    table = dynamodb.Table(table_name)
    item = table.get_item(Key={"job_id": job_id}).get("Item")

    if not item:
        return _response(404, {"error": f"Job {job_id} not found"})

    status = item["status"]

    # Detect stale jobs — if RUNNING/PENDING for too long, report as FAILED
    if status in ("RUNNING", "PENDING"):
        created_at = item.get("created_at", "")
        if created_at:
            try:
                created_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - created_time).total_seconds()
                if elapsed > STALE_JOB_TIMEOUT_SECONDS:
                    status = "FAILED"
            except (ValueError, TypeError):
                pass

    response = {"job_id": item["job_id"], "status": status}
    if status == "COMPLETE":
        response["result"] = item.get("result", {})
    elif status == "FAILED":
        response["error"] = item.get("error", "Job timed out or failed")

    return _response(200, response)


def _handle_worker(event):
    """Async worker — runs the actual agent and stores result."""
    job_id = event.get("job_id")
    request = event.get("request", {})
    table_name = os.environ.get("JOB_TABLE_NAME")

    table = dynamodb.Table(table_name) if table_name else None

    # Idempotency guard: if job already completed (e.g. from a retry), skip
    if table:
        existing = table.get_item(Key={"job_id": job_id}).get("Item", {})
        if existing.get("status") == "COMPLETE":
            logger.info(f"Job {job_id} already complete, skipping retry")
            return {"status": "COMPLETE", "job_id": job_id}

    if table:
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": "RUNNING"},
        )

    try:
        from agent import generate_load_test

        result = generate_load_test(
            spec_content=request.get("spec", ""),
            user_request=request.get("prompt", ""),
            spec_format=request.get("format", "yaml"),
            dependencies=request.get("dependencies", []),
            records=request.get("records", {}),
            context=request.get("context", ""),
        )

        if table:
            table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s = :s, #r = :r, completed_at = :t",
                ExpressionAttributeNames={"#s": "status", "#r": "result"},
                ExpressionAttributeValues={
                    ":s": "COMPLETE",
                    ":r": result,
                    ":t": datetime.now(timezone.utc).isoformat(),
                },
            )

        return {"status": "COMPLETE", "job_id": job_id}

    except Exception as e:
        logger.error(f"Worker failed: {traceback.format_exc()}")
        if table:
            table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s = :s, #e = :e",
                ExpressionAttributeNames={"#s": "status", "#e": "error"},
                ExpressionAttributeValues={":s": "FAILED", ":e": str(e)},
            )
        raise


def _parse_body(event) -> dict:
    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        return body
    except json.JSONDecodeError:
        return {"error": "Invalid JSON body"}


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }
