"""Fargate service — manages AWS ECS Fargate tasks for distributed k6 execution."""

from __future__ import annotations

import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from perfsage_executor.config import get_settings
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


class FargateService:
    """Manages ECS Fargate tasks for running k6 in AWS."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._ecs_client = boto3.client("ecs", region_name=self.settings.aws.region)
        self._logs_client = boto3.client("logs", region_name=self.settings.aws.region)

    def register_task_definition(
        self,
        test_id: str,
        cpu: int = 2048,
        memory: int = 4096,
        environment: dict[str, str] | None = None,
    ) -> str:
        """Register an ECS task definition for k6 execution.

        Args:
            test_id: Test run identifier.
            cpu: CPU units (256, 512, 1024, 2048, 4096).
            memory: Memory in MB.
            environment: Env vars for the container.

        Returns:
            Task definition ARN.
        """
        env_vars = [{"name": k, "value": v} for k, v in (environment or {}).items()]
        env_vars.append({"name": "PERFSAGE_TEST_ID", "value": test_id})

        ecr_image = self._get_ecr_image_uri()
        log_group = f"/perfsage/k6/{test_id}"

        # Ensure log group exists
        self._ensure_log_group(log_group)

        try:
            response = self._ecs_client.register_task_definition(
                family=self.settings.ecs.task_family,
                networkMode="awsvpc",
                requiresCompatibilities=["FARGATE"],
                cpu=str(cpu),
                memory=str(memory),
                executionRoleArn=self._get_execution_role_arn(),
                taskRoleArn=self._get_task_role_arn(),
                containerDefinitions=[
                    {
                        "name": "k6-runner",
                        "image": ecr_image,
                        "essential": True,
                        "environment": env_vars,
                        "stopTimeout": 120,  # Max allowed by Fargate (120s grace for S3 upload)
                        "logConfiguration": {
                            "logDriver": "awslogs",
                            "options": {
                                "awslogs-group": log_group,
                                "awslogs-region": self.settings.aws.region,
                                "awslogs-stream-prefix": "k6",
                            },
                        },
                        "healthCheck": {
                            "command": ["CMD-SHELL", "echo healthy"],
                            "interval": 30,
                            "timeout": 5,
                            "retries": 3,
                            "startPeriod": 10,
                        },
                    }
                ],
                tags=[
                    {"key": "perfsage:test_id", "value": test_id},
                    {"key": "perfsage:component", "value": "k6-runner"},
                ],
            )
            task_def_arn = response["taskDefinition"]["taskDefinitionArn"]
            logger.info(f"Registered task definition: {task_def_arn}")
            return task_def_arn
        except ClientError as e:
            raise RuntimeError(f"Failed to register task definition: {e}") from e

    def run_task(self, task_definition_arn: str, test_id: str) -> dict[str, Any]:
        """Launch a Fargate task.

        Args:
            task_definition_arn: ARN of the registered task definition.
            test_id: Test run identifier.

        Returns:
            Dict with task_arn, cluster_arn, and status.
        """
        try:
            response = self._ecs_client.run_task(
                cluster=self.settings.ecs.cluster,
                taskDefinition=task_definition_arn,
                launchType="FARGATE",
                count=1,
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": self.settings.ecs.subnets,
                        "securityGroups": self.settings.ecs.security_groups,
                        "assignPublicIp": "ENABLED",
                    }
                },
                tags=[
                    {"key": "perfsage:test_id", "value": test_id},
                ],
            )

            if response.get("failures"):
                failure = response["failures"][0]
                raise RuntimeError(f"Fargate task launch failed: {failure.get('reason', 'Unknown')}")

            task = response["tasks"][0]
            task_arn = task["taskArn"]
            logger.info(f"Launched Fargate task: {task_arn}")

            return {
                "task_arn": task_arn,
                "cluster_arn": task["clusterArn"],
                "status": task["lastStatus"],
            }
        except ClientError as e:
            raise RuntimeError(f"Failed to run Fargate task: {e}") from e

    def wait_for_task_running(self, task_arn: str, timeout: int = 120) -> bool:
        """Wait for a Fargate task to reach RUNNING state.

        Args:
            task_arn: The task ARN to monitor.
            timeout: Max seconds to wait.

        Returns:
            True if task is RUNNING, False if timeout or failure.
        """
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_task_status(task_arn)
            if status == "RUNNING":
                logger.info(f"Task {task_arn} is RUNNING")
                return True
            if status in ("STOPPED", "DEPROVISIONING"):
                logger.error(f"Task {task_arn} reached terminal state: {status}")
                return False
            time.sleep(5)

        logger.error(f"Timeout waiting for task {task_arn} to reach RUNNING")
        return False

    def get_task_status(self, task_arn: str) -> str:
        """Get current status of a Fargate task."""
        try:
            response = self._ecs_client.describe_tasks(
                cluster=self.settings.ecs.cluster,
                tasks=[task_arn],
            )
            if response["tasks"]:
                return response["tasks"][0]["lastStatus"]
            return "NOT_FOUND"
        except ClientError:
            return "ERROR"

    def stop_task(self, task_arn: str, reason: str = "PerfSage test termination") -> None:
        """Stop a running Fargate task.

        Args:
            task_arn: The task ARN to stop.
            reason: Reason for stopping.
        """
        try:
            self._ecs_client.stop_task(
                cluster=self.settings.ecs.cluster,
                task=task_arn,
                reason=reason,
            )
            logger.info(f"Stopped Fargate task: {task_arn}")
        except ClientError as e:
            logger.error(f"Failed to stop task {task_arn}: {e}")

    def get_task_logs(self, test_id: str, limit: int = 100) -> list[str]:
        """Retrieve CloudWatch logs for a task.

        Args:
            test_id: Test run identifier (used to derive log group).
            limit: Max number of log events.

        Returns:
            List of log messages.
        """
        log_group = f"/perfsage/k6/{test_id}"
        try:
            response = self._logs_client.filter_log_events(
                logGroupName=log_group,
                limit=limit,
                interleaved=True,
            )
            return [event["message"] for event in response.get("events", [])]
        except ClientError:
            return []

    def _get_ecr_image_uri(self) -> str:
        """Construct the ECR image URI."""
        account_id = self.settings.aws.account_id
        region = self.settings.aws.region
        repo = self.settings.ecs.ecr_repository
        return f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo}:latest"

    def _get_execution_role_arn(self) -> str:
        """Get the ECS task execution role ARN."""
        account_id = self.settings.aws.account_id
        return f"arn:aws:iam::{account_id}:role/perfsage-ecs-execution-role"

    def _get_task_role_arn(self) -> str:
        """Get the ECS task role ARN."""
        account_id = self.settings.aws.account_id
        return f"arn:aws:iam::{account_id}:role/perfsage-ecs-task-role"

    def _ensure_log_group(self, log_group: str) -> None:
        """Create CloudWatch log group if it doesn't exist."""
        try:
            self._logs_client.create_log_group(logGroupName=log_group)
            self._logs_client.put_retention_policy(
                logGroupName=log_group,
                retentionInDays=7,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise
