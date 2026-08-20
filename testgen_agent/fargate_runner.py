"""Fargate entrypoint for TestGen agent.

Downloads input from S3, runs the Strands agent, updates DynamoDB with result.
This replaces the Lambda _handle_worker() function for Fargate execution.
"""
import json
import os
import sys
import signal
import logging
import traceback
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
)

dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")

JOB_ID = os.environ.get("TESTGEN_JOB_ID", "")
TABLE_NAME = os.environ.get("JOB_TABLE_NAME", "")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")


def get_table():
    if TABLE_NAME:
        return dynamodb.Table(TABLE_NAME)
    return None


def update_status(status, error=None, result=None):
    table = get_table()
    if not table:
        return

    try:
        if status == "RUNNING":
            table.update_item(
                Key={"job_id": JOB_ID},
                UpdateExpression="SET #s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": "RUNNING"},
            )
        elif status == "COMPLETE" and result is not None:
            table.update_item(
                Key={"job_id": JOB_ID},
                UpdateExpression="SET #s = :s, #r = :r, completed_at = :t",
                ExpressionAttributeNames={"#s": "status", "#r": "result"},
                ExpressionAttributeValues={
                    ":s": "COMPLETE",
                    ":r": result,
                    ":t": datetime.now(timezone.utc).isoformat(),
                },
            )
        elif status == "FAILED":
            table.update_item(
                Key={"job_id": JOB_ID},
                UpdateExpression="SET #s = :s, #e = :e",
                ExpressionAttributeNames={"#s": "status", "#e": "error"},
                ExpressionAttributeValues={":s": "FAILED", ":e": error or "Unknown error"},
            )
    except Exception as e:
        logger.error(f"Failed to update job status to {status}: {e}")


def sigterm_handler(signum, frame):
    logger.warning(f"SIGTERM received — marking job {JOB_ID} as FAILED")
    update_status("FAILED", error="Container terminated (SIGTERM)")
    sys.exit(1)


signal.signal(signal.SIGTERM, sigterm_handler)


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/testgen/input.json"

    if not JOB_ID:
        logger.error("TESTGEN_JOB_ID not set")
        sys.exit(1)

    logger.info(f"Starting TestGen job: {JOB_ID}")

    # Idempotency guard
    table = get_table()
    if table:
        try:
            existing = table.get_item(Key={"job_id": JOB_ID}).get("Item", {})
            if existing.get("status") == "COMPLETE":
                logger.info(f"Job {JOB_ID} already complete, skipping")
                sys.exit(0)
        except Exception as e:
            logger.warning(f"Failed to check job status, proceeding: {e}")

    update_status("RUNNING")

    try:
        with open(input_path) as f:
            request = json.load(f)

        logger.info(f"Input loaded: spec={len(request.get('spec', ''))} chars, "
                    f"prompt={len(request.get('prompt', ''))} chars")

        from agent import generate_load_test

        result = generate_load_test(
            spec_content=request.get("spec", ""),
            user_request=request.get("prompt", ""),
            spec_format=request.get("format", "yaml"),
            dependencies=request.get("dependencies", []),
            records=request.get("records", {}),
            context=request.get("context", ""),
        )

        update_status("COMPLETE", result=result)
        logger.info(f"Job {JOB_ID} completed successfully. "
                    f"Script length: {len(result.get('script', ''))} chars")

    except Exception as e:
        logger.error(f"Job {JOB_ID} failed: {traceback.format_exc()}")
        update_status("FAILED", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
