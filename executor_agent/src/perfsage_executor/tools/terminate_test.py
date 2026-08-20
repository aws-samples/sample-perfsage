"""terminate_test tool — graceful shutdown, result persistence, and infrastructure cleanup."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from strands import tool

from perfsage_executor.models.test_run import InfraDetails, TestRun, TestStatus, TestSummary
from perfsage_executor.services.docker_service import DockerService
from perfsage_executor.services.dynamodb_service import DynamoDBService
from perfsage_executor.services.fargate_service import FargateService
from perfsage_executor.services.s3_service import S3Service
from perfsage_executor.services.websocket_service import WebSocketService
from perfsage_executor.tools.detect_anomaly import cleanup_engine
from perfsage_executor.tools.execute_test import mark_execution_complete
from perfsage_executor.utils.k6_parser import K6OutputParser
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


@tool
def terminate_test(test_id: str, infrastructure_json: str, reason: str = "normal_completion") -> str:
    """Terminate a running load test, persist results, and clean up infrastructure.

    Handles graceful shutdown (SIGTERM → wait → SIGKILL), collects final metrics,
    uploads to S3, writes summary to DynamoDB, and tears down containers/tasks.

    Args:
        test_id: Unique test run identifier.
        infrastructure_json: JSON string with InfraDetails.
        reason: Termination reason ('normal_completion', 'anomaly_auto_stop', 'user_abort', 'infrastructure_failure').

    Returns:
        JSON string with termination status, S3 URIs, and summary.
    """
    try:
        infra = InfraDetails.model_validate_json(infrastructure_json)
    except Exception as e:
        return json.dumps({"error": f"Invalid infrastructure details: {e}"})

    logger.info(f"Terminating test {test_id} (reason: {reason})")

    try:
        # Step 1: Graceful stop
        if infra.execution_mode == "local":
            exit_code = _stop_local(test_id, infra)
        else:
            exit_code = _stop_fargate(test_id, infra)

        # Step 2: Collect and parse final results
        summary = _collect_results(test_id, infra)

        # Step 3: Upload to S3
        s3_uris = _upload_results(test_id, infra)
        metrics_location = s3_uris.get("base_uri", "")

        # Step 4: Save to DynamoDB
        _save_to_dynamodb(test_id, reason, summary, metrics_location)

        # Step 5: Cleanup infrastructure
        _cleanup_infrastructure(test_id, infra)

        # Step 6: Notify clients
        ws_service = WebSocketService()
        status_map = {
            "normal_completion": "completed",
            "anomaly_auto_stop": "aborted",
            "user_abort": "aborted",
            "infrastructure_failure": "failed",
        }
        ws_service.broadcast_status(test_id, status_map.get(reason, "completed"), reason)

        # Cleanup module-level state
        mark_execution_complete(test_id)
        cleanup_engine(test_id)

        result = {
            "status": "terminated",
            "test_id": test_id,
            "reason": reason,
            "exit_code": exit_code,
            "metrics_location": metrics_location,
            "s3_uris": s3_uris,
            "summary": summary.model_dump() if summary else None,
            "message": f"Test {test_id} terminated successfully. Results stored in S3.",
        }

        logger.info(f"Test {test_id} terminated and results persisted")
        return json.dumps(result, default=str)

    except Exception as e:
        error_result = {
            "status": "error",
            "test_id": test_id,
            "error": str(e),
            "message": f"Termination encountered errors: {e}",
        }
        logger.error(f"Termination error for test {test_id}: {e}")
        return json.dumps(error_result)


def _stop_local(test_id: str, infra: InfraDetails) -> int:
    """Stop local Docker container gracefully."""
    docker_svc = DockerService()

    if not infra.container_id:
        logger.warning("No container_id — nothing to stop")
        return 0

    status = docker_svc.get_container_status(infra.container_id)
    if status == "running":
        # Graceful stop (SIGTERM, wait 30s, then SIGKILL)
        exit_code = docker_svc.stop_container(infra.container_id, timeout=30)
        return exit_code
    elif status == "exited":
        logger.info(f"Container {infra.container_id[:12]} already exited")
        return 0

    return -1


def _stop_fargate(test_id: str, infra: InfraDetails) -> int:
    """Wait for Fargate task to stop naturally (do NOT force-stop — let k6 finish and upload)."""
    fargate_svc = FargateService()

    if not infra.task_arn:
        logger.warning("No task_arn — nothing to stop")
        return 0

    # Wait for task to reach STOPPED state naturally (up to 30 minutes)
    # Do NOT call stop_task — let the container finish its work
    # (k6 exit → summary generation → S3 upload → container exits)
    for _ in range(180):
        status = fargate_svc.get_task_status(infra.task_arn)
        if status in ("STOPPED", "DEPROVISIONING"):
            logger.info(f"Fargate task {infra.task_arn} stopped naturally")
            return 0
        time.sleep(10)

    # Only force-stop as a last resort after 30 minutes
    logger.warning(f"Task {infra.task_arn} still running after 30 min, force-stopping")
    fargate_svc.stop_task(infra.task_arn, reason=f"PerfSage timeout: {test_id}")
    time.sleep(10)
    return 0


def _collect_results(test_id: str, infra: InfraDetails) -> TestSummary | None:
    """Collect and parse final test results."""
    if infra.execution_mode == "local":
        return _collect_local_results(test_id)
    else:
        return _collect_fargate_results(test_id, infra)


def _collect_local_results(test_id: str) -> TestSummary | None:
    """Parse k6 summary from local output directory."""
    output_dir = Path(f"/tmp/perfsage/{test_id}")
    summary_file = output_dir / "summary.json"

    if not summary_file.exists():
        logger.warning(f"No summary file found for test {test_id}")
        return None

    parser = K6OutputParser(test_id)
    summary_data = parser.parse_summary(summary_file.read_text())

    if not summary_data:
        return None

    return TestSummary(
        total_requests=summary_data.get("total_requests", 0),
        total_errors=summary_data.get("total_errors", 0),
        error_rate_pct=summary_data.get("error_rate_pct", 0.0),
        avg_latency_ms=summary_data.get("avg_latency_ms", 0.0),
        p50_latency_ms=summary_data.get("p50_latency_ms", 0.0),
        p90_latency_ms=summary_data.get("p90_latency_ms", 0.0),
        p95_latency_ms=summary_data.get("p95_latency_ms", 0.0),
        p99_latency_ms=summary_data.get("p99_latency_ms", 0.0),
        max_latency_ms=summary_data.get("max_latency_ms", 0.0),
        avg_rps=summary_data.get("avg_rps", 0.0),
    )


def _collect_fargate_results(test_id: str, infra: InfraDetails) -> TestSummary | None:
    """Collect results from Fargate task logs."""
    fargate_svc = FargateService()
    logs = fargate_svc.get_task_logs(test_id, limit=500)

    if not logs:
        return None

    parser = K6OutputParser(test_id)
    for line in logs:
        parser.parse_line(line)

    snapshot = parser.get_snapshot()
    return TestSummary(
        total_requests=snapshot.total_requests,
        total_errors=snapshot.total_errors,
        error_rate_pct=snapshot.error_rate_pct,
        avg_latency_ms=(snapshot.latency_p50_ms + snapshot.latency_p90_ms) / 2,
        p50_latency_ms=snapshot.latency_p50_ms,
        p90_latency_ms=snapshot.latency_p90_ms,
        p95_latency_ms=snapshot.latency_p95_ms,
        p99_latency_ms=snapshot.latency_p99_ms,
        avg_rps=snapshot.rps,
    )


def _upload_results(test_id: str, infra: InfraDetails) -> dict[str, str]:
    """Upload test results to S3."""
    s3_svc = S3Service()
    uris: dict[str, str] = {"base_uri": s3_svc.get_results_uri(test_id)}

    if infra.execution_mode == "local":
        output_dir = f"/tmp/perfsage/{test_id}"
        if Path(output_dir).exists():
            uploaded = s3_svc.upload_test_results(test_id, output_dir)
            uris.update(uploaded)
    else:
        # For Fargate, logs are already in CloudWatch
        # Upload a summary artifact
        fargate_svc = FargateService()
        logs = fargate_svc.get_task_logs(test_id, limit=1000)
        if logs:
            s3_svc.upload_json(test_id, {"logs": logs}, "task_logs.json")

    return uris


def _save_to_dynamodb(
    test_id: str,
    reason: str,
    summary: TestSummary | None,
    metrics_location: str,
) -> None:
    """Persist test run summary to DynamoDB."""
    dynamodb_svc = DynamoDBService()

    status_map = {
        "normal_completion": "completed",
        "anomaly_auto_stop": "aborted",
        "user_abort": "aborted",
        "infrastructure_failure": "failed",
    }

    kwargs: dict[str, Any] = {
        "ended_at": int(time.time() * 1000),
        "metrics_location": metrics_location,
    }
    if summary:
        kwargs["summary"] = summary.model_dump()

    dynamodb_svc.update_status(test_id, status_map.get(reason, "completed"), **kwargs)


def _cleanup_infrastructure(test_id: str, infra: InfraDetails) -> None:
    """Tear down infrastructure resources."""
    if infra.execution_mode == "local":
        docker_svc = DockerService()
        if infra.container_id:
            docker_svc.remove_container(infra.container_id)
        docker_svc.cleanup_network()
    # Fargate tasks are already stopped — no further cleanup needed
    logger.info(f"Infrastructure cleaned up for test {test_id}")
