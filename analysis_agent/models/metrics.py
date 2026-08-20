from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LatencyMetrics:
    p50_ms: float
    p90_ms: float
    p99_ms: float
    mean_ms: float
    max_ms: float


@dataclass
class TestMetrics:
    total_requests: int
    successful_requests: int
    failed_requests: int
    rps_mean: float
    rps_max: float
    latency: LatencyMetrics
    error_rate: float
    duration_seconds: float
    vus_max: int
    timestamps: list = field(default_factory=list)
    latency_over_time: list = field(default_factory=list)
    rps_over_time: list = field(default_factory=list)
    errors_over_time: list = field(default_factory=list)
    error_codes: dict = field(default_factory=dict)


@dataclass
class SLODefinition:
    name: str
    metric: str
    threshold: float
    operator: str  # "lt", "gt", "lte", "gte"


@dataclass
class SLOResult:
    slo: SLODefinition
    actual_value: float
    passed: bool


@dataclass
class RootCause:
    pattern: str
    evidence: str
    confidence: str  # "high", "medium", "low"


@dataclass
class Recommendation:
    title: str
    description: str
    priority: str  # "critical", "high", "medium", "low"
    category: str  # "infrastructure", "configuration", "code", "architecture"


@dataclass
class RunComparison:
    metric: str
    current_value: float
    baseline_value: float
    change_percent: float
    direction: str  # "improved", "degraded", "unchanged"


@dataclass
class AnalysisReport:
    summary: str
    metrics: TestMetrics
    root_causes: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    slo_results: list = field(default_factory=list)
    comparisons: list = field(default_factory=list)
    overall_verdict: str = "unknown"  # "pass", "fail", "warning"
