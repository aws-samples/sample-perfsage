"""detect_anomaly tool — monitors metrics in real-time and flags anomalous patterns."""

from __future__ import annotations

import json
from typing import Any

from strands import tool

from perfsage_executor.models.metrics import AnomalyEvent, AnomalySeverity, MetricSnapshot
from perfsage_executor.models.test_config import TestConfig, ThresholdConfig
from perfsage_executor.services.cloudwatch_service import CloudWatchService
from perfsage_executor.services.websocket_service import WebSocketService
from perfsage_executor.utils.logger import get_logger
from perfsage_executor.utils.threshold_engine import ThresholdEngine

logger = get_logger(__name__)

# Module-level registry of threshold engines per test
_engines: dict[str, ThresholdEngine] = {}


@tool
def detect_anomaly(test_id: str, snapshots_json: str, thresholds_json: str) -> str:
    """Analyze metric snapshots for anomalies using rule-based detection.

    Evaluates metrics against:
    1. User-defined threshold breaches
    2. Sudden latency spikes (>3x in 10s window)
    3. Error rate bursts (<1% to >5% in 5s)
    4. Connection exhaustion patterns
    5. Flatline detection (0 RPS with active VUs)

    Args:
        test_id: Unique test run identifier.
        snapshots_json: JSON array of MetricSnapshot objects from stream_metrics.
        thresholds_json: JSON string of ThresholdConfig (p99_latency_ms, error_rate_pct, etc.)

    Returns:
        JSON string with detected anomalies, severity, and recommended actions.
    """
    try:
        snapshots_data = json.loads(snapshots_json)
        snapshots = [MetricSnapshot.model_validate(s) for s in snapshots_data]
    except Exception as e:
        return json.dumps({"error": f"Invalid snapshots data: {e}"})

    try:
        thresholds = ThresholdConfig.model_validate_json(thresholds_json)
    except Exception as e:
        return json.dumps({"error": f"Invalid thresholds config: {e}"})

    # Get or create threshold engine for this test
    if test_id not in _engines:
        _engines[test_id] = ThresholdEngine(thresholds=thresholds)
    engine = _engines[test_id]

    # Evaluate each snapshot
    all_new_anomalies: list[AnomalyEvent] = []
    for snapshot in snapshots:
        new_anomalies = engine.evaluate(snapshot)
        all_new_anomalies.extend(new_anomalies)

    # Publish anomalies to WebSocket and CloudWatch
    if all_new_anomalies:
        _publish_anomalies(test_id, all_new_anomalies)

    # Determine if auto-stop should be triggered
    should_terminate = any(
        a.severity == AnomalySeverity.CRITICAL and a.auto_action == "terminate"
        for a in all_new_anomalies
    )

    result: dict[str, Any] = {
        "test_id": test_id,
        "anomalies_detected": len(all_new_anomalies),
        "should_terminate": should_terminate,
        "total_anomalies_in_test": len(engine.all_anomalies),
        "has_critical": engine.has_critical,
        "anomalies": [
            {
                "timestamp": a.timestamp.isoformat(),
                "severity": a.severity.value,
                "type": a.anomaly_type.value,
                "description": a.description,
                "metric": a.metric_name,
                "value": a.current_value,
                "expected_range": list(a.expected_range),
                "action": a.auto_action,
            }
            for a in all_new_anomalies
        ],
    }

    if should_terminate:
        result["message"] = "CRITICAL anomaly detected — recommend immediate test termination"
        logger.warning(f"Critical anomaly in test {test_id} — termination recommended")
    elif all_new_anomalies:
        result["message"] = f"Detected {len(all_new_anomalies)} anomalie(s) — monitoring continues"
    else:
        result["message"] = "No anomalies detected — metrics are within normal ranges"

    return json.dumps(result, default=str)


def _publish_anomalies(test_id: str, anomalies: list[AnomalyEvent]) -> None:
    """Publish anomalies to WebSocket and CloudWatch."""
    ws_service = WebSocketService()
    cw_service = CloudWatchService()

    for anomaly in anomalies:
        anomaly_data = {
            "timestamp": anomaly.timestamp.isoformat(),
            "severity": anomaly.severity.value,
            "type": anomaly.anomaly_type.value,
            "description": anomaly.description,
            "metric": anomaly.metric_name,
            "value": anomaly.current_value,
        }
        ws_service.broadcast_anomaly(test_id, anomaly_data)
        cw_service.publish_anomaly(test_id, anomaly.anomaly_type.value, anomaly.severity.value)


def cleanup_engine(test_id: str) -> None:
    """Remove the threshold engine for a completed test."""
    _engines.pop(test_id, None)
