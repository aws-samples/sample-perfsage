"""Test configuration model — input contract from Agent 1 (TestGen Agent)."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    """Execution environment for the load test."""

    LOCAL = "local"
    FARGATE = "fargate"


class ThresholdConfig(BaseModel):
    """Performance threshold definitions for pass/fail evaluation."""

    p99_latency_ms: float | None = Field(default=None, description="Maximum acceptable p99 latency in milliseconds")
    p95_latency_ms: float | None = Field(default=None, description="Maximum acceptable p95 latency in milliseconds")
    p90_latency_ms: float | None = Field(default=None, description="Maximum acceptable p90 latency in milliseconds")
    error_rate_pct: float | None = Field(default=None, description="Maximum acceptable error rate percentage")
    min_rps: float | None = Field(default=None, description="Minimum acceptable requests per second")


class EndpointRelationship(BaseModel):
    """Defines a dependency/relationship between API endpoints for execution ordering.

    This allows the test to respect data dependencies — e.g., create a user before
    placing an order, or authenticate before accessing protected resources.
    """

    source: str = Field(description="Source endpoint (must execute first). E.g., 'POST /users'")
    target: str = Field(description="Target endpoint (depends on source). E.g., 'POST /orders'")
    relationship: str = Field(
        default="depends_on",
        description="Relationship type: 'depends_on', 'feeds_into', 'sequential', 'auth_required'",
    )
    data_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="How data flows from source response to target request. E.g., {'$.id': '$.user_id'}",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of why this dependency exists",
    )


class TestScenarioParams(BaseModel):
    """User-provided scenario parameters passed from Agent 1.

    These are the essential parameters the user originally gives to the TestGen Agent,
    forwarded to the Executor for context and runtime decisions.
    """

    target_url: str | None = Field(default=None, description="Base URL of the target API under test")
    auth_type: str | None = Field(
        default=None,
        description="Authentication mechanism: 'none', 'bearer_token', 'api_key', 'basic', 'oauth2'",
    )
    auth_token: str | None = Field(default=None, description="Auth token or API key value (if applicable)")
    content_type: str = Field(default="application/json", description="Default content type for requests")
    think_time_ms: int | None = Field(
        default=None,
        description="Simulated user think time between requests in milliseconds",
    )
    ramp_pattern: str | None = Field(
        default=None,
        description="Ramp pattern: 'linear', 'step', 'spike', 'soak'. Determines how VUs scale up.",
    )
    geographic_distribution: str | None = Field(
        default=None,
        description="Where to simulate traffic from (e.g., 'us-east-1', 'multi-region')",
    )
    custom_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Custom HTTP headers to include in all requests",
    )
    payload_size: str | None = Field(
        default=None,
        description="Expected payload size category: 'small' (<1KB), 'medium' (1-100KB), 'large' (>100KB)",
    )
    scenario_description: str | None = Field(
        default=None,
        description="Original natural language description from the user (e.g., 'Test checkout flow with 2000 users')",
    )


class TestConfig(BaseModel):
    """Configuration for a single load test execution.

    This is the primary input to the Executor Agent, typically produced by Agent 1 (TestGen).
    """

    test_id: str = Field(default_factory=lambda: f"run-{uuid.uuid4().hex[:12]}", description="Unique test run ID")
    script_path: str = Field(description="Path or S3 URI to the k6 test script")
    virtual_users: int = Field(ge=1, le=10000, description="Number of virtual users (VUs)")
    duration: str = Field(description="Test duration (e.g., '5m', '30s', '1h')")
    ramp_up: str = Field(default="0s", description="Ramp-up period (e.g., '2m', '30s')")
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig, description="Pass/fail thresholds")
    execution_mode: ExecutionMode = Field(default=ExecutionMode.LOCAL, description="Where to run: local Docker or AWS Fargate")
    tags: dict[str, str] = Field(default_factory=dict, description="Metadata tags for the test run")
    environment_vars: dict[str, str] = Field(default_factory=dict, description="Additional env vars to pass to k6")
    auto_stop_on_anomaly: bool = Field(default=True, description="Auto-terminate on critical anomaly detection")
    fargate_cpu: int = Field(default=512, description="Fargate task CPU units (256, 512, 1024, 2048, 4096)")
    fargate_memory: int = Field(default=1024, description="Fargate task memory in MB")

    # --- Optional fields from Agent 1 / user input ---

    total_records: int | None = Field(
        default=None,
        description="Total number of records/iterations to generate across all VUs. "
        "When set, the test will aim for this total request count rather than running purely by duration.",
    )
    endpoint_relationships: list[EndpointRelationship] | None = Field(
        default=None,
        description="Ordered relationships between endpoints defining execution sequence. "
        "E.g., create user → authenticate → place order. The executor uses this to "
        "validate the k6 script respects data dependencies.",
    )
    scenario_params: TestScenarioParams | None = Field(
        default=None,
        description="User-provided scenario parameters forwarded from Agent 1 (TestGen). "
        "Includes auth config, target URL, think time, ramp pattern, etc.",
    )

    def to_k6_env(self) -> dict[str, str]:
        """Convert config to environment variables for k6 execution."""
        env: dict[str, Any] = {
            "K6_VUS": str(self.virtual_users),
            "K6_DURATION": self.duration,
            "PERFSAGE_TEST_ID": self.test_id,
        }

        # Pass total_records so the k6 script can use it for iteration control
        if self.total_records is not None:
            env["PERFSAGE_TOTAL_RECORDS"] = str(self.total_records)

        # Pass scenario params as env vars for k6 script consumption
        if self.scenario_params:
            if self.scenario_params.target_url:
                env["TARGET_URL"] = self.scenario_params.target_url
            if self.scenario_params.auth_token:
                env["AUTH_TOKEN"] = self.scenario_params.auth_token
            if self.scenario_params.auth_type:
                env["AUTH_TYPE"] = self.scenario_params.auth_type
            if self.scenario_params.think_time_ms is not None:
                env["THINK_TIME_MS"] = str(self.scenario_params.think_time_ms)

        env.update(self.environment_vars)
        return env
