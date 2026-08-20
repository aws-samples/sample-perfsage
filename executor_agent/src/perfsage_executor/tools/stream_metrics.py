"""stream_metrics tool — reads k6 output, aggregates metrics, and pushes to WebSocket + CloudWatch."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from strands import tool

from perfsage_executor.models.metrics import MetricSnapshot
from perfsage_executor.models.test_run import InfraDetails
from perfsage_executor.services.cloudwatch_service import CloudWatchService
from perfsage_executor.services.docker_service import DockerService
from perfsage_executor.services.fargate_service import FargateService
from perfsage_executor.services.websocket_service import WebSocketService
from perfsage_executor.tools.execute_test import get_active_execution
from perfsage_executor.utils.k6_parser import K6OutputParser
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


@tool
def stream_metrics(test_id: str, infrastructure_json: str, duration_seconds: int = 10) -> str:
    """Stream real-time metrics from a running k6 test.

    Reads k6 JSON output, aggregates into 1-second buckets, and pushes to WebSocket
    clients and CloudWatch. Returns the latest metric snapshots.

    Args:
        test_id: Unique test run identifier.
        infrastructure_json: JSON string with InfraDetails.
        duration_seconds: How many seconds of metrics to collect before returning (default 10).

    Returns:
        JSON string with collected metric snapshots and streaming status.
    """
    try:
        infra = InfraDetails.model_validate_json(infrastructure_json)
    except Exception as e:
        return json.dumps({"error": f"Invalid infrastructure details: {e}"})

    logger.info(f"Streaming metrics for test {test_id} ({duration_seconds}s window)")

    try:
        if infra.execution_mode == "local":
            snapshots = _stream_local(test_id, infra, duration_seconds)
        else:
            snapshots = _stream_fargate(test_id, infra, duration_seconds)

        # Publish to WebSocket and CloudWatch
        ws_service = WebSocketService()
        cw_service = CloudWatchService()

        clients_notified = 0
        for snapshot in snapshots:
            clients_notified += ws_service.broadcast_metrics(test_id, snapshot)
            cw_service.publish_snapshot(snapshot)

        result = {
            "status": "streaming",
            "test_id": test_id,
            "snapshots_collected": len(snapshots),
            "clients_notified": clients_notified,
            "latest_snapshot": snapshots[-1].to_websocket_message() if snapshots else None,
            "message": f"Collected {len(snapshots)} metric snapshots over {duration_seconds}s",
        }

        return json.dumps(result, default=str)

    except Exception as e:
        error_result = {
            "status": "error",
            "test_id": test_id,
            "error": str(e),
        }
        logger.error(f"Metric streaming failed for test {test_id}: {e}")
        return json.dumps(error_result)


def _stream_local(test_id: str, infra: InfraDetails, duration_seconds: int) -> list[MetricSnapshot]:
    """Stream metrics from local Docker container's k6 JSON output file."""
    output_dir = f"/tmp/perfsage/{test_id}"
    metrics_file = Path(output_dir) / "metrics.json"
    parser = K6OutputParser(test_id)
    snapshots: list[MetricSnapshot] = []

    # Wait for metrics file to appear (k6 might still be starting)
    wait_start = time.time()
    while not metrics_file.exists() and time.time() - wait_start < 15:
        time.sleep(1)

    if not metrics_file.exists():
        # Fall back to container logs
        docker_svc = DockerService()
        if infra.container_id:
            status = docker_svc.get_container_status(infra.container_id)
            if status == "exited":
                # Test already completed — read final file
                return _read_final_metrics(test_id, parser)
        logger.warning(f"Metrics file not found: {metrics_file}")
        return snapshots

    # Read new lines from metrics file for the specified duration
    start_time = time.time()
    last_position = 0

    while time.time() - start_time < duration_seconds:
        try:
            with open(metrics_file, "r") as f:
                f.seek(last_position)
                lines = f.readlines()
                last_position = f.tell()

            for line in lines:
                parser.parse_line(line.strip())

            # Generate snapshot every second
            snapshot = parser.get_snapshot()
            if snapshot.total_requests > 0 or snapshot.active_vus > 0:
                snapshots.append(snapshot)

        except (OSError, IOError) as e:
            logger.debug(f"Error reading metrics file: {e}")

        time.sleep(1)

    return snapshots


def _stream_fargate(test_id: str, infra: InfraDetails, duration_seconds: int) -> list[MetricSnapshot]:
    """Stream metrics from Fargate task via CloudWatch logs."""
    fargate_svc = FargateService()
    parser = K6OutputParser(test_id)
    snapshots: list[MetricSnapshot] = []

    start_time = time.time()

    while time.time() - start_time < duration_seconds:
        # Pull latest logs
        log_lines = fargate_svc.get_task_logs(test_id, limit=50)

        for line in log_lines:
            parser.parse_line(line.strip())

        snapshot = parser.get_snapshot()
        if snapshot.total_requests > 0 or snapshot.active_vus > 0:
            snapshots.append(snapshot)

        time.sleep(1)

    return snapshots


def _read_final_metrics(test_id: str, parser: K6OutputParser) -> list[MetricSnapshot]:
    """Read the complete metrics file after test completion."""
    output_dir = f"/tmp/perfsage/{test_id}"
    metrics_file = Path(output_dir) / "metrics.json"
    snapshots: list[MetricSnapshot] = []

    if not metrics_file.exists():
        return snapshots

    with open(metrics_file, "r") as f:
        for line in f:
            parser.parse_line(line.strip())

    snapshot = parser.get_snapshot()
    if snapshot.total_requests > 0:
        snapshots.append(snapshot)

    return snapshots
