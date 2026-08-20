"""Metric data models for real-time streaming and anomaly detection."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class MetricPoint(BaseModel):
    """A single metric data point from k6 output."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metric_name: str = Field(description="Metric name (e.g., http_req_duration, http_reqs, errors, vus)")
    value: float = Field(description="Metric value")
    tags: dict[str, str] = Field(default_factory=dict, description="Metric tags (endpoint, method, status)")
    percentiles: dict[str, float] | None = Field(default=None, description="Percentile values (p50, p90, p95, p99)")


class MetricSnapshot(BaseModel):
    """Aggregated metric snapshot for real-time streaming (1-second bucket)."""

    test_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    rps: float = Field(default=0.0, description="Requests per second")
    latency_p50_ms: float = Field(default=0.0, description="p50 latency in milliseconds")
    latency_p90_ms: float = Field(default=0.0, description="p90 latency in milliseconds")
    latency_p95_ms: float = Field(default=0.0, description="p95 latency in milliseconds")
    latency_p99_ms: float = Field(default=0.0, description="p99 latency in milliseconds")
    error_rate_pct: float = Field(default=0.0, description="Error rate percentage")
    active_vus: int = Field(default=0, description="Currently active virtual users")
    total_requests: int = Field(default=0, description="Cumulative total requests")
    total_errors: int = Field(default=0, description="Cumulative total errors")

    def to_websocket_message(self) -> dict:
        """Format for WebSocket transmission."""
        return {
            "test_id": self.test_id,
            "timestamp": self.timestamp.isoformat(),
            "metrics": {
                "rps": round(self.rps, 2),
                "latency_p50_ms": round(self.latency_p50_ms, 2),
                "latency_p90_ms": round(self.latency_p90_ms, 2),
                "latency_p95_ms": round(self.latency_p95_ms, 2),
                "latency_p99_ms": round(self.latency_p99_ms, 2),
                "error_rate_pct": round(self.error_rate_pct, 3),
                "active_vus": self.active_vus,
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
            },
        }


class AnomalySeverity(str, Enum):
    """Severity level for detected anomalies."""

    WARNING = "warning"
    CRITICAL = "critical"


class AnomalyType(str, Enum):
    """Types of anomalies the detector can identify."""

    THRESHOLD_BREACH = "threshold_breach"
    SPIKE = "spike"
    ERROR_BURST = "error_burst"
    CONNECTION_EXHAUSTION = "connection_exhaustion"
    FLATLINE = "flatline"


class AnomalyEvent(BaseModel):
    """An anomaly detected during test execution."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: AnomalySeverity
    anomaly_type: AnomalyType
    description: str = Field(description="Human-readable description of the anomaly")
    metric_name: str = Field(description="Which metric triggered the anomaly")
    current_value: float = Field(description="The value that triggered detection")
    expected_range: tuple[float, float] = Field(description="Expected (low, high) range")
    auto_action: str = Field(default="none", description="Action taken: 'none' or 'terminate'")
