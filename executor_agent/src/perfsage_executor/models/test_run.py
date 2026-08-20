"""Test run state model — tracks lifecycle of a load test execution."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from perfsage_executor.models.metrics import AnomalyEvent


class TestStatus(str, Enum):
    """Lifecycle states of a test run."""

    PENDING = "pending"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class InfraDetails(BaseModel):
    """Infrastructure details for the execution environment."""

    execution_mode: str = Field(description="'local' or 'fargate'")
    container_id: str | None = Field(default=None, description="Docker container ID (local mode)")
    task_arn: str | None = Field(default=None, description="ECS task ARN (fargate mode)")
    cluster_arn: str | None = Field(default=None, description="ECS cluster ARN (fargate mode)")
    network_id: str | None = Field(default=None, description="Docker network ID (local mode)")
    log_group: str | None = Field(default=None, description="CloudWatch log group (fargate mode)")
    public_ip: str | None = Field(default=None, description="Public IP of the task (if applicable)")


class TestSummary(BaseModel):
    """Aggregated test results summary stored in DynamoDB."""

    total_requests: int = 0
    total_errors: int = 0
    error_rate_pct: float = 0.0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p90_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    avg_rps: float = 0.0
    peak_rps: float = 0.0
    peak_vus: int = 0
    thresholds_passed: bool = True
    threshold_details: dict[str, Any] = Field(default_factory=dict)
    anomalies_detected: int = 0


class TestRun(BaseModel):
    """Full state of a test run through its lifecycle."""

    test_id: str
    status: TestStatus = TestStatus.PENDING
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    infrastructure: InfraDetails | None = None
    metrics_location: str | None = Field(default=None, description="S3 URI for raw metrics")
    summary: TestSummary | None = None
    anomalies: list[AnomalyEvent] = Field(default_factory=list)
    config_snapshot: dict[str, Any] = Field(default_factory=dict, description="Snapshot of test config at run time")
    error_message: str | None = None

    def mark_provisioning(self, infra: InfraDetails) -> None:
        """Transition to provisioning state."""
        self.status = TestStatus.PROVISIONING
        self.infrastructure = infra

    def mark_running(self) -> None:
        """Transition to running state."""
        self.status = TestStatus.RUNNING

    def mark_completed(self, summary: TestSummary, metrics_location: str) -> None:
        """Transition to completed state with results."""
        self.status = TestStatus.COMPLETED
        self.ended_at = datetime.now(timezone.utc)
        self.summary = summary
        self.metrics_location = metrics_location

    def mark_failed(self, error: str) -> None:
        """Transition to failed state."""
        self.status = TestStatus.FAILED
        self.ended_at = datetime.now(timezone.utc)
        self.error_message = error

    def mark_aborted(self, reason: str) -> None:
        """Transition to aborted state."""
        self.status = TestStatus.ABORTED
        self.ended_at = datetime.now(timezone.utc)
        self.error_message = reason

    def add_anomaly(self, anomaly: AnomalyEvent) -> None:
        """Record a detected anomaly."""
        self.anomalies.append(anomaly)

    def to_dynamodb_item(self) -> dict[str, Any]:
        """Serialize for DynamoDB storage."""
        item: dict[str, Any] = {
            "test_id": self.test_id,
            "status": self.status.value,
            "started_at": int(self.started_at.timestamp() * 1000),
            "created_at": int(self.started_at.timestamp() * 1000),
        }
        if self.ended_at:
            item["ended_at"] = int(self.ended_at.timestamp() * 1000)
        if self.infrastructure:
            item["infrastructure"] = self.infrastructure.model_dump()
        if self.metrics_location:
            item["metrics_location"] = self.metrics_location
        if self.summary:
            item["summary"] = self.summary.model_dump()
        if self.anomalies:
            item["anomalies"] = [a.model_dump() for a in self.anomalies]
            item["anomalies_count"] = len(self.anomalies)
        if self.config_snapshot:
            item["config_snapshot"] = self.config_snapshot
        if self.error_message:
            item["error_message"] = self.error_message
        return item
