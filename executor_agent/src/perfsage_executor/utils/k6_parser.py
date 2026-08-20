"""Parser for k6 JSON output format — converts k6 metric lines into MetricPoint models."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from perfsage_executor.models.metrics import MetricPoint, MetricSnapshot
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


class K6OutputParser:
    """Parses k6 JSON output stream line-by-line into structured metrics.

    k6 JSON output format (one JSON object per line):
    {"type":"Point","data":{"time":"2026-06-08T10:00:01.000Z","value":123.45,"tags":{"url":"..."}},
     "metric":"http_req_duration","type":"Point"}
    """

    def __init__(self, test_id: str) -> None:
        self.test_id = test_id
        self._total_requests: int = 0
        self._total_errors: int = 0
        self._latency_values: list[float] = []
        self._current_vus: int = 0
        self._window_requests: int = 0
        self._window_start: datetime | None = None

    def parse_line(self, line: str) -> MetricPoint | None:
        """Parse a single line of k6 JSON output into a MetricPoint.

        Args:
            line: Raw JSON line from k6 output.

        Returns:
            MetricPoint if successfully parsed, None otherwise.
        """
        if not line.strip():
            return None

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.debug(f"Skipping non-JSON line: {line[:100]}")
            return None

        record_type = data.get("type")
        if record_type != "Point":
            return None

        metric_name = data.get("metric", "")
        metric_data = data.get("data", {})

        timestamp_str = metric_data.get("time", "")
        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            timestamp = datetime.now(timezone.utc)

        value = float(metric_data.get("value", 0))
        tags = metric_data.get("tags", {})

        # Track cumulative metrics
        self._update_accumulators(metric_name, value, tags)

        return MetricPoint(
            timestamp=timestamp,
            metric_name=metric_name,
            value=value,
            tags=tags,
        )

    def _update_accumulators(self, metric_name: str, value: float, tags: dict[str, Any]) -> None:
        """Update internal counters based on metric type."""
        if metric_name == "http_reqs":
            self._total_requests += int(value)
            self._window_requests += int(value)
        elif metric_name == "http_req_failed":
            if value > 0:
                self._total_errors += int(value)
        elif metric_name == "http_req_duration":
            self._latency_values.append(value)
        elif metric_name == "vus":
            self._current_vus = int(value)

    def get_snapshot(self) -> MetricSnapshot:
        """Generate an aggregated snapshot from accumulated metrics.

        Resets the sliding window after generating snapshot.
        """
        latency_sorted = sorted(self._latency_values) if self._latency_values else [0.0]
        n = len(latency_sorted)

        snapshot = MetricSnapshot(
            test_id=self.test_id,
            timestamp=datetime.now(timezone.utc),
            rps=float(self._window_requests),
            latency_p50_ms=latency_sorted[int(n * 0.50)] if n > 0 else 0.0,
            latency_p90_ms=latency_sorted[int(n * 0.90)] if n > 0 else 0.0,
            latency_p95_ms=latency_sorted[int(n * 0.95)] if n > 0 else 0.0,
            latency_p99_ms=latency_sorted[min(int(n * 0.99), n - 1)] if n > 0 else 0.0,
            error_rate_pct=(self._total_errors / self._total_requests * 100) if self._total_requests > 0 else 0.0,
            active_vus=self._current_vus,
            total_requests=self._total_requests,
            total_errors=self._total_errors,
        )

        # Reset window counters
        self._window_requests = 0
        self._latency_values = []

        return snapshot

    def parse_summary(self, summary_json: str) -> dict[str, Any]:
        """Parse k6 summary JSON export (end-of-test summary).

        Args:
            summary_json: Content of k6 --summary-export file.

        Returns:
            Dictionary with parsed summary metrics.
        """
        try:
            data = json.loads(summary_json)
        except json.JSONDecodeError:
            logger.error("Failed to parse k6 summary JSON")
            return {}

        metrics = data.get("metrics", {})
        result: dict[str, Any] = {}

        # Extract key metrics from summary
        if "http_req_duration" in metrics:
            duration = metrics["http_req_duration"]
            values = duration.get("values", {})
            result["avg_latency_ms"] = values.get("avg", 0)
            result["p90_latency_ms"] = values.get("p(90)", 0)
            result["p95_latency_ms"] = values.get("p(95)", 0)
            result["p99_latency_ms"] = values.get("p(99)", 0)
            result["max_latency_ms"] = values.get("max", 0)
            result["p50_latency_ms"] = values.get("med", 0)

        if "http_reqs" in metrics:
            reqs = metrics["http_reqs"]
            values = reqs.get("values", {})
            result["total_requests"] = int(values.get("count", 0))
            result["avg_rps"] = values.get("rate", 0)

        if "http_req_failed" in metrics:
            failed = metrics["http_req_failed"]
            values = failed.get("values", {})
            result["total_errors"] = int(values.get("passes", 0))
            total_reqs = result.get("total_requests", 1)
            result["error_rate_pct"] = (result["total_errors"] / total_reqs * 100) if total_reqs > 0 else 0

        return result
