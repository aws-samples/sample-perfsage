"""Unit tests for the threshold/anomaly detection engine."""

import pytest

from perfsage_executor.models.metrics import AnomalySeverity, AnomalyType, MetricSnapshot
from perfsage_executor.models.test_config import ThresholdConfig
from perfsage_executor.utils.threshold_engine import ThresholdEngine


@pytest.fixture
def thresholds():
    return ThresholdConfig(
        p99_latency_ms=500.0,
        p95_latency_ms=300.0,
        error_rate_pct=5.0,
        min_rps=10.0,
    )


@pytest.fixture
def engine(thresholds):
    return ThresholdEngine(thresholds=thresholds, window_size=30)


def make_snapshot(
    test_id: str = "test-001",
    rps: float = 100.0,
    p99: float = 100.0,
    p95: float = 80.0,
    error_rate: float = 0.5,
    vus: int = 50,
) -> MetricSnapshot:
    return MetricSnapshot(
        test_id=test_id,
        rps=rps,
        latency_p50_ms=p99 * 0.4,
        latency_p90_ms=p99 * 0.7,
        latency_p95_ms=p95,
        latency_p99_ms=p99,
        error_rate_pct=error_rate,
        active_vus=vus,
        total_requests=10000,
        total_errors=int(10000 * error_rate / 100),
    )


class TestThresholdBreach:
    def test_no_anomaly_within_thresholds(self, engine):
        snapshot = make_snapshot(p99=200.0, error_rate=1.0)
        anomalies = engine.evaluate(snapshot)
        assert len(anomalies) == 0

    def test_p99_breach(self, engine):
        snapshot = make_snapshot(p99=600.0)
        anomalies = engine.evaluate(snapshot)
        assert any(a.anomaly_type == AnomalyType.THRESHOLD_BREACH for a in anomalies)
        assert any(a.metric_name == "latency_p99_ms" for a in anomalies)
        assert any(a.severity == AnomalySeverity.CRITICAL for a in anomalies)

    def test_error_rate_breach(self, engine):
        snapshot = make_snapshot(error_rate=7.0)
        anomalies = engine.evaluate(snapshot)
        assert any(
            a.anomaly_type == AnomalyType.THRESHOLD_BREACH and a.metric_name == "error_rate_pct"
            for a in anomalies
        )

    def test_rps_below_minimum(self, engine):
        snapshot = make_snapshot(rps=5.0, vus=50)
        anomalies = engine.evaluate(snapshot)
        assert any(a.metric_name == "rps" for a in anomalies)


class TestSpikeDetection:
    def test_detects_latency_spike(self, engine):
        # Feed 5 normal snapshots
        for _ in range(5):
            engine.evaluate(make_snapshot(p99=100.0))

        # Feed 5 more and then a spike
        for _ in range(5):
            engine.evaluate(make_snapshot(p99=100.0))

        # Now spike to 4x
        anomalies = engine.evaluate(make_snapshot(p99=400.0))
        assert any(a.anomaly_type == AnomalyType.SPIKE for a in anomalies)

    def test_no_spike_for_gradual_increase(self, engine):
        # Gradually increasing latency (no sudden spike)
        for i in range(15):
            anomalies = engine.evaluate(make_snapshot(p99=100.0 + i * 10))

        # Last evaluation should not trigger spike (1.5x over baseline, not 3x)
        # The gradual increase means the spike check won't fire
        spike_anomalies = [a for a in anomalies if a.anomaly_type == AnomalyType.SPIKE]
        assert len(spike_anomalies) == 0


class TestErrorBurst:
    def test_detects_error_burst(self, engine):
        # Feed low error rate
        for _ in range(3):
            engine.evaluate(make_snapshot(error_rate=0.5))

        # Sudden burst
        for _ in range(2):
            anomalies = engine.evaluate(make_snapshot(error_rate=8.0))

        assert any(a.anomaly_type == AnomalyType.ERROR_BURST for a in anomalies)

    def test_no_burst_if_already_high(self, engine):
        # Already high error rate — not a burst
        for _ in range(5):
            anomalies = engine.evaluate(make_snapshot(error_rate=6.0))

        burst_anomalies = [a for a in anomalies if a.anomaly_type == AnomalyType.ERROR_BURST]
        assert len(burst_anomalies) == 0


class TestFlatline:
    def test_detects_flatline(self, engine):
        # VUs active but 0 RPS
        for _ in range(5):
            anomalies = engine.evaluate(make_snapshot(rps=0.0, vus=100))

        assert any(a.anomaly_type == AnomalyType.FLATLINE for a in anomalies)

    def test_no_flatline_if_vus_zero(self, engine):
        # 0 RPS and 0 VUs is normal (test hasn't started yet)
        for _ in range(5):
            anomalies = engine.evaluate(make_snapshot(rps=0.0, vus=0))

        flatline_anomalies = [a for a in anomalies if a.anomaly_type == AnomalyType.FLATLINE]
        assert len(flatline_anomalies) == 0


class TestAutoAction:
    def test_critical_anomaly_recommends_terminate(self, engine):
        snapshot = make_snapshot(p99=600.0)
        anomalies = engine.evaluate(snapshot)
        critical = [a for a in anomalies if a.severity == AnomalySeverity.CRITICAL]
        assert all(a.auto_action == "terminate" for a in critical)

    def test_has_critical_property(self, engine):
        assert not engine.has_critical
        engine.evaluate(make_snapshot(p99=600.0))
        assert engine.has_critical
