"""provision_infrastructure tool — spins up Docker (local) or Fargate (AWS) execution environment."""

from __future__ import annotations

import json
from typing import Any

from strands import tool

from perfsage_executor.config import get_settings
from perfsage_executor.models.test_config import ExecutionMode, TestConfig
from perfsage_executor.models.test_run import InfraDetails, TestRun, TestStatus
from perfsage_executor.services.docker_service import DockerService
from perfsage_executor.services.fargate_service import FargateService
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


@tool
def provision_infrastructure(test_config_json: str) -> str:
    """Provision execution infrastructure for a k6 load test.

    Sets up either a local Docker container or AWS Fargate task based on the execution mode
    specified in the test configuration. Returns infrastructure details needed by execute_test.

    Args:
        test_config_json: JSON string of the TestConfig (script_path, virtual_users, duration, execution_mode, etc.)

    Returns:
        JSON string with infrastructure details (container_id/task_arn, network info, status).
    """
    try:
        config = TestConfig.model_validate_json(test_config_json)
    except Exception as e:
        return json.dumps({"error": f"Invalid test configuration: {e}"})

    settings = get_settings()
    logger.info(f"Provisioning infrastructure for test {config.test_id} in {config.execution_mode.value} mode")

    try:
        if config.execution_mode == ExecutionMode.LOCAL:
            infra = _provision_local(config)
        else:
            infra = _provision_fargate(config)

        # Build test run state
        test_run = TestRun(
            test_id=config.test_id,
            status=TestStatus.PROVISIONING,
            infrastructure=infra,
            config_snapshot=config.model_dump(),
        )

        result = {
            "status": "provisioned",
            "test_id": config.test_id,
            "execution_mode": config.execution_mode.value,
            "infrastructure": infra.model_dump(),
            "test_run": test_run.model_dump(),
        }

        logger.info(f"Infrastructure provisioned for test {config.test_id}")
        return json.dumps(result, default=str)

    except Exception as e:
        error_result = {
            "status": "error",
            "test_id": config.test_id,
            "error": str(e),
            "execution_mode": config.execution_mode.value,
        }
        logger.error(f"Provisioning failed for test {config.test_id}: {e}")
        return json.dumps(error_result)


def _provision_local(config: TestConfig) -> InfraDetails:
    """Provision local Docker infrastructure."""
    docker_svc = DockerService()

    # Pull image and create network
    docker_svc.pull_k6_image()
    network_id = docker_svc.ensure_network()

    # Start k6 container
    container = docker_svc.start_k6_container(
        script_path=config.script_path,
        test_id=config.test_id,
        environment=config.to_k6_env(),
        output_dir=f"/tmp/perfsage/{config.test_id}",
    )

    return InfraDetails(
        execution_mode="local",
        container_id=container.id,
        network_id=network_id,
    )


def _provision_fargate(config: TestConfig) -> InfraDetails:
    """Provision AWS Fargate infrastructure.

    Uploads the k6 script to S3 so the Fargate container can download and execute it.
    """
    from perfsage_executor.services.s3_service import S3Service

    fargate_svc = FargateService()
    s3_svc = S3Service()

    # Upload k6 script to S3 so the container can access it
    script_s3_uri = s3_svc.upload_file(config.test_id, config.script_path, "test.js")
    logger.info(f"Uploaded k6 script to {script_s3_uri}")

    # Add S3 script URI to environment so the container entrypoint downloads it
    env = config.to_k6_env()
    settings = get_settings()
    env["K6_SCRIPT_S3_URI"] = script_s3_uri
    env["PERFSAGE_S3_BUCKET"] = settings.s3.bucket
    env["PERFSAGE_S3_PREFIX"] = settings.s3.prefix

    # Calculate setupTimeout based on record count — prevents k6 from killing
    # setup() after its default 60s when creating many records
    env["K6_SETUP_TIMEOUT"] = _calculate_setup_timeout(config)

    # Fallback TARGET_URL if Agent 1 didn't provide one
    if "TARGET_URL" not in env:
        env["TARGET_URL"] = "https://httpbin.org"

    # Also set BASE_URL — Agent 1's generated scripts use this variable name
    if "TARGET_URL" in env:
        env["BASE_URL"] = env["TARGET_URL"]

    # Register task definition
    task_def_arn = fargate_svc.register_task_definition(
        test_id=config.test_id,
        cpu=config.fargate_cpu,
        memory=config.fargate_memory,
        environment=env,
    )

    # Run the task
    task_info = fargate_svc.run_task(task_def_arn, config.test_id)

    # Wait for RUNNING state
    is_running = fargate_svc.wait_for_task_running(task_info["task_arn"])
    if not is_running:
        raise RuntimeError(f"Fargate task failed to reach RUNNING state: {task_info['task_arn']}")

    return InfraDetails(
        execution_mode="fargate",
        task_arn=task_info["task_arn"],
        cluster_arn=task_info["cluster_arn"],
        log_group=f"/perfsage/k6/{config.test_id}",
    )


def _calculate_setup_timeout(config: TestConfig) -> str:
    """Calculate a deliberately GENEROUS k6 setupTimeout based on record count.

    We intentionally err on the side of LARGER timeouts. A setup() that is killed
    mid-seed wastes the entire run, so it is far cheaper to over-allocate the
    window and let the container finish seeding (and upload results) naturally.

    Seeding uses http.batch() with inter-batch sleeps, so effective throughput is
    much lower than raw RPS. We assume a conservative ~20 records/sec and add a
    large fixed buffer plus a comfortable floor. TARGET_RPS can override the
    throughput assumption, but the default is intentionally low (generous), and
    smaller values only make the window bigger.
    """
    # Conservative effective throughput (records/sec). Lower => larger timeout.
    try:
        target_rps = int(config.environment_vars.get("TARGET_RPS", "20"))
    except (TypeError, ValueError):
        target_rps = 20
    if target_rps <= 0:
        target_rps = 20

    # Large fixed headroom on top of the seeding estimate, and a generous floor
    # so even small seeds get a comfortable window for setup + natural shutdown.
    # Includes an extra 4 min (240 s) safety margin on top for corner cases.
    BUFFER_SECONDS = 540   # 5 min headroom + 4 min extra safety margin
    FLOOR_SECONDS = 540    # never less than 9 min

    if config.total_records and config.total_records > 0:
        seconds = max(FLOOR_SECONDS, int(config.total_records / target_rps) + BUFFER_SECONDS)
        return f"{seconds}s"
    return f"{FLOOR_SECONDS}s"
