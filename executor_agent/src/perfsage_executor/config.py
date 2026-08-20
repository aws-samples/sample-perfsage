"""Configuration management — auto-discovers settings from AWS CloudFormation when possible."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env if it exists
_env_file = Path(__file__).parent.parent.parent.parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    load_dotenv()  # Try default locations


class AWSConfig(BaseModel):
    """AWS-specific configuration."""

    region: str = Field(default="us-east-1")
    account_id: str = Field(default="")
    profile: str = Field(default="default")


class S3Config(BaseModel):
    """S3 storage configuration."""

    bucket: str = Field(default="perfsage-results")
    prefix: str = Field(default="runs")

    def results_uri(self, test_id: str) -> str:
        """Get the S3 URI for a test run's results."""
        return f"s3://{self.bucket}/{self.prefix}/{test_id}"


class DynamoDBConfig(BaseModel):
    """DynamoDB configuration."""

    table_name: str = Field(default="perfsage-test-runs")
    connections_table: str = Field(default="perfsage-ws-connections")


class ECSConfig(BaseModel):
    """ECS/Fargate configuration."""

    cluster: str = Field(default="perfsage-executor")
    task_family: str = Field(default="perfsage-k6-runner")
    ecr_repository: str = Field(default="perfsage/k6-runner")
    subnets: list[str] = Field(default_factory=list)
    security_groups: list[str] = Field(default_factory=list)


class WebSocketConfig(BaseModel):
    """WebSocket API configuration."""

    api_url: str = Field(default="")
    stage: str = Field(default="prod")


class DockerConfig(BaseModel):
    """Local Docker configuration."""

    network_name: str = Field(default="perfsage-net")
    k6_image: str = Field(default="grafana/k6:latest")


class AgentConfig(BaseModel):
    """Agent runtime configuration."""

    execution_mode: str = Field(default="local")
    log_level: str = Field(default="INFO")
    anomaly_auto_stop: bool = Field(default=True)
    model_id: str = Field(default="anthropic.claude-3-5-sonnet-20241022-v2:0")
    model_region: str = Field(default="us-east-1")


class Settings(BaseModel):
    """Root configuration combining all sub-configs."""

    aws: AWSConfig = Field(default_factory=AWSConfig)
    s3: S3Config = Field(default_factory=S3Config)
    dynamodb: DynamoDBConfig = Field(default_factory=DynamoDBConfig)
    ecs: ECSConfig = Field(default_factory=ECSConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)


def _discover_aws_config() -> dict:
    """Auto-discover AWS configuration from CloudFormation outputs and STS.

    This eliminates the need for users to manually look up subnet IDs,
    security groups, and WebSocket URLs after deployment.
    """
    discovered: dict = {}

    try:
        import boto3

        region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")

        # Get account ID from STS
        sts = boto3.client("sts", region_name=region)
        identity = sts.get_caller_identity()
        discovered["account_id"] = identity["Account"]
        discovered["region"] = region

        # Try loading from .cdk-outputs.json first (fastest)
        cdk_outputs_file = Path(__file__).parent.parent.parent.parent / ".cdk-outputs.json"
        if cdk_outputs_file.exists():
            with open(cdk_outputs_file) as f:
                outputs = json.load(f)

            net_outputs = outputs.get("PerfSageNetworking", {})
            exe_outputs = outputs.get("PerfSageExecution", {})
            storage_outputs = outputs.get("PerfSageStorage", {})

            discovered["websocket_url"] = net_outputs.get("WebSocketManagementUrl", "")
            discovered["subnets"] = net_outputs.get("PrivateSubnets", "")
            discovered["security_group"] = exe_outputs.get("SecurityGroupId", "")
            discovered["s3_bucket"] = storage_outputs.get("ResultsBucketName",
                                                          f"perfsage-results-{discovered['account_id']}-{region}")
            return discovered

        # Fallback: query CloudFormation stacks directly
        cf = boto3.client("cloudformation", region_name=region)

        # Query networking stack
        try:
            response = cf.describe_stacks(StackName="PerfSageNetworking")
            for output in response["Stacks"][0].get("Outputs", []):
                if output["OutputKey"] == "WebSocketManagementUrl":
                    discovered["websocket_url"] = output["OutputValue"]
                elif output["OutputKey"] == "PrivateSubnets":
                    discovered["subnets"] = output["OutputValue"]
        except Exception:
            pass

        # Query execution stack
        try:
            response = cf.describe_stacks(StackName="PerfSageExecution")
            for output in response["Stacks"][0].get("Outputs", []):
                if output["OutputKey"] == "SecurityGroupId":
                    discovered["security_group"] = output["OutputValue"]
        except Exception:
            pass

        # Query storage stack
        try:
            response = cf.describe_stacks(StackName="PerfSageStorage")
            for output in response["Stacks"][0].get("Outputs", []):
                if output["OutputKey"] == "ResultsBucketName":
                    discovered["s3_bucket"] = output["OutputValue"]
        except Exception:
            discovered["s3_bucket"] = f"perfsage-results-{discovered['account_id']}-{region}"

    except Exception:
        # Auto-discovery failed — fall back to env vars / defaults
        pass

    return discovered


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings from environment, auto-discovering from AWS when values are missing.

    Priority order:
    1. Explicit environment variables (always win)
    2. Auto-discovered from CloudFormation / .cdk-outputs.json
    3. Hardcoded defaults
    """
    # Check if we need auto-discovery (subnets/security groups missing)
    needs_discovery = (
        not os.getenv("PERFSAGE_FARGATE_SUBNETS")
        and os.getenv("PERFSAGE_EXECUTION_MODE", "local") == "fargate"
    )

    discovered: dict = {}
    if needs_discovery:
        discovered = _discover_aws_config()

    region = os.getenv("AWS_REGION", discovered.get("region", "us-east-1"))
    account_id = os.getenv("AWS_ACCOUNT_ID", discovered.get("account_id", ""))
    s3_bucket = os.getenv("PERFSAGE_S3_BUCKET", discovered.get("s3_bucket", f"perfsage-results-{account_id}-{region}" if account_id else "perfsage-results"))

    # Subnets and security groups: env var > discovered > empty
    subnets_str = os.getenv("PERFSAGE_FARGATE_SUBNETS", discovered.get("subnets", ""))
    sg_str = os.getenv("PERFSAGE_FARGATE_SECURITY_GROUPS", discovered.get("security_group", ""))

    return Settings(
        aws=AWSConfig(
            region=region,
            account_id=account_id,
            profile=os.getenv("AWS_PROFILE", "default"),
        ),
        s3=S3Config(
            bucket=s3_bucket,
            prefix=os.getenv("PERFSAGE_S3_PREFIX", "runs"),
        ),
        dynamodb=DynamoDBConfig(
            table_name=os.getenv("PERFSAGE_DYNAMODB_TABLE", "perfsage-test-runs"),
            connections_table=os.getenv("PERFSAGE_DYNAMODB_CONNECTIONS_TABLE", "perfsage-ws-connections"),
        ),
        ecs=ECSConfig(
            cluster=os.getenv("PERFSAGE_ECS_CLUSTER", "perfsage-executor"),
            task_family=os.getenv("PERFSAGE_ECS_TASK_FAMILY", "perfsage-k6-runner"),
            ecr_repository=os.getenv("PERFSAGE_ECR_REPOSITORY", "perfsage/k6-runner"),
            subnets=[s.strip() for s in subnets_str.split(",") if s.strip()],
            security_groups=[s.strip() for s in sg_str.split(",") if s.strip()],
        ),
        websocket=WebSocketConfig(
            api_url=os.getenv("PERFSAGE_WEBSOCKET_API_URL", discovered.get("websocket_url", "")),
            stage=os.getenv("PERFSAGE_WEBSOCKET_STAGE", "prod"),
        ),
        docker=DockerConfig(
            network_name=os.getenv("PERFSAGE_DOCKER_NETWORK", "perfsage-net"),
            k6_image=os.getenv("PERFSAGE_K6_IMAGE", "grafana/k6:latest"),
        ),
        agent=AgentConfig(
            execution_mode=os.getenv("PERFSAGE_EXECUTION_MODE", "local"),
            log_level=os.getenv("PERFSAGE_LOG_LEVEL", "INFO"),
            anomaly_auto_stop=os.getenv("PERFSAGE_ANOMALY_AUTO_STOP", "true").lower() == "true",
            model_id=os.getenv("PERFSAGE_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
            model_region=os.getenv("PERFSAGE_MODEL_REGION", region),
        ),
    )
