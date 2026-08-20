"""execute_test tool — runs k6 within the provisioned environment and monitors execution."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from strands import tool

from perfsage_executor.models.test_run import InfraDetails, TestStatus
from perfsage_executor.services.docker_service import DockerService
from perfsage_executor.services.fargate_service import FargateService
from perfsage_executor.utils.k6_parser import K6OutputParser
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)

# Module-level registry of active test executions
_active_executions: dict[str, dict[str, Any]] = {}


@tool
def execute_test(test_id: str, infrastructure_json: str) -> str:
    """Execute a k6 load test within the provisioned infrastructure.

    Starts the k6 test and begins monitoring execution. The test runs asynchronously;
    use stream_metrics to get real-time updates and detect_anomaly to monitor health.

    Args:
        test_id: Unique test run identifier from provision_infrastructure.
        infrastructure_json: JSON string with InfraDetails from provision_infrastructure.

    Returns:
        JSON string with execution status and monitoring details.
    """
    try:
        infra = InfraDetails.model_validate_json(infrastructure_json)
    except Exception as e:
        return json.dumps({"error": f"Invalid infrastructure details: {e}"})

    logger.info(f"Starting test execution: {test_id} (mode: {infra.execution_mode})")

    try:
        if infra.execution_mode == "local":
            result = _execute_local(test_id, infra)
        else:
            result = _execute_fargate(test_id, infra)

        # Register active execution
        _active_executions[test_id] = {
            "infrastructure": infra,
            "status": "running",
            "started_at": time.time(),
            "output_dir": f"/tmp/perfsage/{test_id}",
        }

        return json.dumps(result, default=str)

    except Exception as e:
        error_result = {
            "status": "error",
            "test_id": test_id,
            "error": str(e),
        }
        logger.error(f"Execution failed for test {test_id}: {e}")
        return json.dumps(error_result)


def _execute_local(test_id: str, infra: InfraDetails) -> dict[str, Any]:
    """Monitor a locally running Docker container."""
    docker_svc = DockerService()

    if not infra.container_id:
        raise RuntimeError("No container_id in infrastructure details")

    # Check container is running
    status = docker_svc.get_container_status(infra.container_id)
    if status not in ("running", "created"):
        raise RuntimeError(f"Container is in unexpected state: {status}")

    output_dir = f"/tmp/perfsage/{test_id}"
    metrics_file = Path(output_dir) / "metrics.json"

    return {
        "status": "running",
        "test_id": test_id,
        "execution_mode": "local",
        "container_id": infra.container_id,
        "output_dir": output_dir,
        "metrics_file": str(metrics_file),
        "message": f"k6 test is running in container {infra.container_id[:12]}. Use stream_metrics to monitor.",
    }


def _execute_fargate(test_id: str, infra: InfraDetails) -> dict[str, Any]:
    """Monitor a Fargate task execution."""
    fargate_svc = FargateService()

    if not infra.task_arn:
        raise RuntimeError("No task_arn in infrastructure details")

    # Verify task is running
    status = fargate_svc.get_task_status(infra.task_arn)
    if status != "RUNNING":
        raise RuntimeError(f"Fargate task is in unexpected state: {status}")

    return {
        "status": "running",
        "test_id": test_id,
        "execution_mode": "fargate",
        "task_arn": infra.task_arn,
        "log_group": infra.log_group,
        "message": f"k6 test is running in Fargate task. Use stream_metrics to monitor.",
    }


def get_active_execution(test_id: str) -> dict[str, Any] | None:
    """Get details of an active test execution.

    Args:
        test_id: Test run identifier.

    Returns:
        Active execution details or None.
    """
    return _active_executions.get(test_id)


def mark_execution_complete(test_id: str) -> None:
    """Mark a test execution as complete and remove from active registry."""
    if test_id in _active_executions:
        _active_executions[test_id]["status"] = "completed"
