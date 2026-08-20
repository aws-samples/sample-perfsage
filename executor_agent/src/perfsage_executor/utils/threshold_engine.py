"""Threshold evaluation engine — detects anomalies using sliding window and rule-based strategies."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from perfsage_executor.models.metrics import AnomalyEvent, AnomalySeverity, AnomalyType, MetricSnapshot
from perfsage_executor.models.test_config import ThresholdConfig
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)


class ThresholdEngine:
    """Rule-based anomaly detection using sliding window over metric snapshots.

    Detection strategies:
    1. Threshold breach — metric exceeds user-defined limits
    2. Sudden spike — metric increases >3x within 10-second window
    3. Error burst — error rate jumps from <1% to >5% in 5 seconds
    4. Connection exhaustion — connection errors + increasing latency
    5. Flatline — RPS drops to 0 while VUs are active
    """

    def __init__(
        self,
        thresholds: ThresholdConfig,
        window_size: int = 30,
        spike_multiplier: float = 3.0,
    ) -> None:
        """Initialize the threshold engine.

        Args:
            thresholds: User-defined performance thresholds.
            window_size: Number of snapshots to keep in sliding window (at 1s intervals = seconds).
            spike_multiplier: Multiplier for spike detection (default 3x).
        """
        self.thresholds = thresholds
        self.window_size = window_size
        self.spike_multiplier = spike_multiplier
        self._window: deque[MetricSnapshot] = deque(maxlen=window_size)
        self._anomalies: list[AnomalyEvent] = []

    def evaluate(self, snapshot: MetricSnapshot) -> list[AnomalyEvent]:
        """Evaluate a new metric snapshot against all detection strategies.

        Args:
            snapshot: Latest metric snapshot to evaluate.

        Returns:
            List of newly detected anomalies (may be empty).
        """
        self._window.append(snapshot)
        new_anomalies: list[AnomalyEvent] = []

        # Strategy 1: Threshold breach
        new_anomalies.extend(self._check_threshold_breach(snapshot))

        # Strategy 2: Sudden spike (need at least 10 samples)
        if len(self._window) >= 10:
            new_anomalies.extend(self._check_spike(snapshot))

        # Strategy 3: Error burst (need at least 5 samples)
        if len(self._window) >= 5:
            new_anomalies.extend(self._check_error_burst(snapshot))

        # Strategy 4: Connection exhaustion pattern
        if len(self._window) >= 10:
            new_anomalies.extend(self._check_connection_exhaustion())

        # Strategy 5: Flatline
        if len(self._window) >= 5:
            new_anomalies.extend(self._check_flatline(snapshot))

        self._anomalies.extend(new_anomalies)
        return new_anomalies

    def _check_threshold_breach(self, snapshot: MetricSnapshot) -> list[AnomalyEvent]:
        """Check if any metric exceeds user-defined thresholds."""
        anomalies: list[AnomalyEvent] = []

        if self.thresholds.p99_latency_ms and snapshot.latency_p99_ms > self.thresholds.p99_latency_ms:
            anomalies.append(
                AnomalyEvent(
                    timestamp=datetime.now(timezone.utc),
                    severity=AnomalySeverity.CRITICAL,
                    anomaly_type=AnomalyType.THRESHOLD_BREACH,
                    description=f"p99 latency ({snapshot.latency_p99_ms:.1f}ms) exceeds threshold ({self.thresholds.p99_latency_ms}ms)",
                    metric_name="latency_p99_ms",
                    current_value=snapshot.latency_p99_ms,
                    expected_range=(0, self.thresholds.p99_latency_ms),
                    auto_action="terminate",
                )
            )

        if self.thresholds.p95_latency_ms and snapshot.latency_p95_ms > self.thresholds.p95_latency_ms:
            anomalies.append(
                AnomalyEvent(
                    timestamp=datetime.now(timezone.utc),
                    severity=AnomalySeverity.WARNING,
                    anomaly_type=AnomalyType.THRESHOLD_BREACH,
                    description=f"p95 latency ({snapshot.latency_p95_ms:.1f}ms) exceeds threshold ({self.thresholds.p95_latency_ms}ms)",
                    metric_name="latency_p95_ms",
                    current_value=snapshot.latency_p95_ms,
                    expected_range=(0, self.thresholds.p95_latency_ms),
                    auto_action="none",
                )
            )

        if self.thresholds.error_rate_pct and snapshot.error_rate_pct > self.thresholds.error_rate_pct:
            anomalies.append(
                AnomalyEvent(
                    timestamp=datetime.now(timezone.utc),
                    severity=AnomalySeverity.CRITICAL,
                    anomaly_type=AnomalyType.THRESHOLD_BREACH,
                    description=f"Error rate ({snapshot.error_rate_pct:.2f}%) exceeds threshold ({self.thresholds.error_rate_pct}%)",
                    metric_name="error_rate_pct",
                    current_value=snapshot.error_rate_pct,
                    expected_range=(0, self.thresholds.error_rate_pct),
                    auto_action="terminate",
                )
            )

        if self.thresholds.min_rps and snapshot.rps < self.thresholds.min_rps and snapshot.active_vus > 0:
            anomalies.append(
                AnomalyEvent(
                    timestamp=datetime.now(timezone.utc),
                    severity=AnomalySeverity.WARNING,
                    anomaly_type=AnomalyType.THRESHOLD_BREACH,
                    description=f"RPS ({snapshot.rps:.1f}) below minimum threshold ({self.thresholds.min_rps})",
                    metric_name="rps",
                    current_value=snapshot.rps,
                    expected_range=(self.thresholds.min_rps, float("inf")),
                    auto_action="none",
                )
            )

        return anomalies

    def _check_spike(self, snapshot: MetricSnapshot) -> list[AnomalyEvent]:
        """Detect sudden latency spike (>3x increase in 10-second window)."""
        anomalies: list[AnomalyEvent] = []

        # Compare current p99 with average of 10 seconds ago
        window_10s = list(self._window)[-10:]
        if len(window_10s) < 10:
            return anomalies

        baseline_p99 = sum(s.latency_p99_ms for s in window_10s[:5]) / 5

        if baseline_p99 > 0 and snapshot.latency_p99_ms > baseline_p99 * self.spike_multiplier:
            anomalies.append(
                AnomalyEvent(
                    timestamp=datetime.now(timezone.utc),
                    severity=AnomalySeverity.CRITICAL,
                    anomaly_type=AnomalyType.SPIKE,
                    description=f"Sudden latency spike: p99 jumped from {baseline_p99:.1f}ms to {snapshot.latency_p99_ms:.1f}ms ({snapshot.latency_p99_ms / baseline_p99:.1f}x increase)",
                    metric_name="latency_p99_ms",
                    current_value=snapshot.latency_p99_ms,
                    expected_range=(0, baseline_p99 * self.spike_multiplier),
                    auto_action="terminate",
                )
            )

        return anomalies

    def _check_error_burst(self, snapshot: MetricSnapshot) -> list[AnomalyEvent]:
        """Detect error burst — error rate jumps from <1% to >5% within 5 seconds."""
        anomalies: list[AnomalyEvent] = []

        window_5s = list(self._window)[-5:]
        if len(window_5s) < 5:
            return anomalies

        # Check if earlier samples were low error and current is high
        earlier_error_rate = sum(s.error_rate_pct for s in window_5s[:3]) / 3

        if earlier_error_rate < 1.0 and snapshot.error_rate_pct > 5.0:
            anomalies.append(
                AnomalyEvent(
                    timestamp=datetime.now(timezone.utc),
                    severity=AnomalySeverity.CRITICAL,
                    anomaly_type=AnomalyType.ERROR_BURST,
                    description=f"Error burst detected: error rate jumped from {earlier_error_rate:.2f}% to {snapshot.error_rate_pct:.2f}% in 5 seconds",
                    metric_name="error_rate_pct",
                    current_value=snapshot.error_rate_pct,
                    expected_range=(0, 5.0),
                    auto_action="terminate",
                )
            )

        return anomalies

    def _check_connection_exhaustion(self) -> list[AnomalyEvent]:
        """Detect connection exhaustion pattern — errors rising + latency increasing simultaneously."""
        anomalies: list[AnomalyEvent] = []

        window_10s = list(self._window)[-10:]
        if len(window_10s) < 10:
            return anomalies

        first_half = window_10s[:5]
        second_half = window_10s[5:]

        avg_latency_first = sum(s.latency_p99_ms for s in first_half) / 5
        avg_latency_second = sum(s.latency_p99_ms for s in second_half) / 5
        avg_errors_first = sum(s.error_rate_pct for s in first_half) / 5
        avg_errors_second = sum(s.error_rate_pct for s in second_half) / 5

        # Both latency AND errors increasing steadily
        latency_increasing = avg_latency_second > avg_latency_first * 1.5
        errors_increasing = avg_errors_second > avg_errors_first * 2 and avg_errors_second > 2.0

        if latency_increasing and errors_increasing:
            anomalies.append(
                AnomalyEvent(
                    timestamp=datetime.now(timezone.utc),
                    severity=AnomalySeverity.CRITICAL,
                    anomaly_type=AnomalyType.CONNECTION_EXHAUSTION,
                    description=f"Connection exhaustion pattern: latency up {avg_latency_second / avg_latency_first:.1f}x and error rate rising to {avg_errors_second:.1f}%",
                    metric_name="connection_health",
                    current_value=avg_errors_second,
                    expected_range=(0, 2.0),
                    auto_action="terminate",
                )
            )

        return anomalies

    def _check_flatline(self, snapshot: MetricSnapshot) -> list[AnomalyEvent]:
        """Detect flatline — RPS drops to 0 while VUs are still active (target is down)."""
        anomalies: list[AnomalyEvent] = []

        # Check last 5 seconds of RPS all being 0 while VUs > 0
        window_5s = list(self._window)[-5:]
        if len(window_5s) < 5:
            return anomalies

        all_zero_rps = all(s.rps == 0 for s in window_5s)
        vus_active = any(s.active_vus > 0 for s in window_5s)

        if all_zero_rps and vus_active:
            anomalies.append(
                AnomalyEvent(
                    timestamp=datetime.now(timezone.utc),
                    severity=AnomalySeverity.CRITICAL,
                    anomaly_type=AnomalyType.FLATLINE,
                    description=f"Flatline detected: RPS is 0 for 5+ seconds while {snapshot.active_vus} VUs are active — target may be down",
                    metric_name="rps",
                    current_value=0.0,
                    expected_range=(1.0, float("inf")),
                    auto_action="terminate",
                )
            )

        return anomalies

    @property
    def all_anomalies(self) -> list[AnomalyEvent]:
        """Get all anomalies detected during this engine's lifetime."""
        return self._anomalies.copy()

    @property
    def has_critical(self) -> bool:
        """Check if any critical anomaly has been detected."""
        return any(a.severity == AnomalySeverity.CRITICAL for a in self._anomalies)
