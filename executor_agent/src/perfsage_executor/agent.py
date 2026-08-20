"""Deterministic orchestrator for the PerfSage Executor Agent.

Replaces the LLM-based Strands Agent with direct function calls.
The orchestration sequence (provision → execute → monitor → terminate) is
entirely predictable — no LLM reasoning needed. This eliminates ~15-20 min
of Bedrock inference overhead per test run.
"""

from __future__ import annotations

import json
import time

from perfsage_executor.config import get_settings
from perfsage_executor.models.metrics import MetricSnapshot
from perfsage_executor.models.test_config import TestConfig
from perfsage_executor.models.test_run import InfraDetails
from perfsage_executor.services.fargate_service import FargateService
from perfsage_executor.tools.detect_anomaly import detect_anomaly, cleanup_engine
from perfsage_executor.tools.execute_test import execute_test, mark_execution_complete
from perfsage_executor.tools.provision_infrastructure import provision_infrastructure
from perfsage_executor.tools.stream_metrics import stream_metrics
from perfsage_executor.tools.terminate_test import terminate_test
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


def run_test(test_config_json: str) -> str:
    """Run a complete load test using deterministic orchestration.

    Executes the standard sequence without any LLM calls:
    1. Provision infrastructure (Fargate)
    2. Confirm execution started
    3. Monitor metrics + detect anomalies in a loop
    4. Terminate and persist results

    Args:
        test_config_json: JSON string of TestConfig.

    Returns:
        JSON string with final test status and results.
    """
    config = TestConfig.model_validate_json(test_config_json)
    logger.info(f"[Orchestrator] Starting test {config.test_id}")

    # ─── Step 1: Provision Infrastructure ───────────────────────────────
    logger.info(f"[Orchestrator] Provisioning infrastructure for {config.test_id}")
    infra_json = provision_infrastructure(test_config_json)
    infra_result = json.loads(infra_json)

    if infra_result.get("status") == "error":
        logger.error(f"[Orchestrator] Provisioning failed: {infra_result.get('error')}")
        return infra_json

    infra = InfraDetails.model_validate(infra_result["infrastructure"])
    infra_json_str = infra.model_dump_json()
    logger.info(f"[Orchestrator] Infrastructure provisioned: {infra.execution_mode}")

    # ─── Step 2: Execute Test ───────────────────────────────────────────
    logger.info(f"[Orchestrator] Starting k6 execution for {config.test_id}")
    exec_json = execute_test(config.test_id, infra_json_str)
    exec_result = json.loads(exec_json)

    if exec_result.get("status") == "error":
        logger.error(f"[Orchestrator] Execution failed: {exec_result.get('error')}")
        # Attempt cleanup
        terminate_test(config.test_id, infra_json_str, "infrastructure_failure")
        return exec_json

    logger.info(f"[Orchestrator] Test running, entering monitoring loop")

    # ─── Step 3: Monitor Loop (no LLM, pure Python) ────────────────────
    fargate_svc = FargateService()
    thresholds_json = config.thresholds.model_dump_json()
    loop_count = 0
    max_loops = 900  # Safety: exit after ~2.5 hours (900 × 10s)

    while loop_count < max_loops:
        loop_count += 1

        # Check if Fargate task has stopped
        if infra.task_arn:
            task_status = fargate_svc.get_task_status(infra.task_arn)
            if task_status in ("STOPPED", "DEPROVISIONING", "NOT_FOUND"):
                logger.info(f"[Orchestrator] Fargate task stopped (status: {task_status})")
                break

        # Collect metrics for 10 seconds
        try:
            metrics_json = stream_metrics(config.test_id, infra_json_str, duration_seconds=10)
            metrics_result = json.loads(metrics_json)
        except Exception as e:
            logger.warning(f"[Orchestrator] Metrics streaming error: {e}")
            time.sleep(10)
            continue

        # Check for anomalies
        latest_snapshot = metrics_result.get("latest_snapshot")
        if latest_snapshot and metrics_result.get("snapshots_collected", 0) > 0:
            try:
                anomaly_json = detect_anomaly(
                    config.test_id,
                    json.dumps([latest_snapshot]),
                    thresholds_json,
                )
                anomaly_result = json.loads(anomaly_json)

                if anomaly_result.get("should_terminate") and config.auto_stop_on_anomaly:
                    logger.warning(
                        f"[Orchestrator] Critical anomaly detected, auto-stopping test {config.test_id}"
                    )
                    result = terminate_test(config.test_id, infra_json_str, "anomaly_auto_stop")
                    return result
            except Exception as e:
                logger.warning(f"[Orchestrator] Anomaly detection error: {e}")

        # Brief pause if metrics collection was very fast (shouldn't happen, but safety)
        time.sleep(1)

    # ─── Step 4: Terminate and Persist Results ──────────────────────────
    logger.info(f"[Orchestrator] Terminating test {config.test_id} (normal completion)")
    result = terminate_test(config.test_id, infra_json_str, "normal_completion")

    logger.info(f"[Orchestrator] Test {config.test_id} complete")
    return result
