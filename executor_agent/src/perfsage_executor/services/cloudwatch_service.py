"""CloudWatch service — publishes custom metrics for dashboards and alarms."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from perfsage_executor.config import get_settings
from perfsage_executor.models.metrics import MetricSnapshot
from perfsage_executor.utils.logger import get_logger

logger = get_logger(__name__)

NAMESPACE = "PerfSage/LoadTests"


class CloudWatchService:
    """Publishes k6 metrics to CloudWatch for monitoring and dashboards."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = boto3.client("cloudwatch", region_name=self.settings.aws.region)

    def publish_snapshot(self, snapshot: MetricSnapshot) -> None:
        """Publish a metric snapshot as CloudWatch custom metrics.

        Args:
            snapshot: Aggregated metric snapshot.
        """
        dimensions = [
            {"Name": "TestId", "Value": snapshot.test_id},
        ]

        metric_data: list[dict[str, Any]] = [
            {
                "MetricName": "RequestsPerSecond",
                "Timestamp": snapshot.timestamp,
                "Value": snapshot.rps,
                "Unit": "Count/Second",
                "Dimensions": dimensions,
            },
            {
                "MetricName": "LatencyP50",
                "Timestamp": snapshot.timestamp,
                "Value": snapshot.latency_p50_ms,
                "Unit": "Milliseconds",
                "Dimensions": dimensions,
            },
            {
                "MetricName": "LatencyP90",
                "Timestamp": snapshot.timestamp,
                "Value": snapshot.latency_p90_ms,
                "Unit": "Milliseconds",
                "Dimensions": dimensions,
            },
            {
                "MetricName": "LatencyP95",
                "Timestamp": snapshot.timestamp,
                "Value": snapshot.latency_p95_ms,
                "Unit": "Milliseconds",
                "Dimensions": dimensions,
            },
            {
                "MetricName": "LatencyP99",
                "Timestamp": snapshot.timestamp,
                "Value": snapshot.latency_p99_ms,
                "Unit": "Milliseconds",
                "Dimensions": dimensions,
            },
            {
                "MetricName": "ErrorRate",
                "Timestamp": snapshot.timestamp,
                "Value": snapshot.error_rate_pct,
                "Unit": "Percent",
                "Dimensions": dimensions,
            },
            {
                "MetricName": "ActiveVUs",
                "Timestamp": snapshot.timestamp,
                "Value": float(snapshot.active_vus),
                "Unit": "Count",
                "Dimensions": dimensions,
            },
        ]

        try:
            # CloudWatch accepts up to 1000 metric data points per PutMetricData call
            self._client.put_metric_data(
                Namespace=NAMESPACE,
                MetricData=metric_data,
            )
        except ClientError as e:
            logger.warning(f"Failed to publish CloudWatch metrics: {e}")

    def publish_anomaly(self, test_id: str, anomaly_type: str, severity: str) -> None:
        """Publish an anomaly event as a CloudWatch metric.

        Args:
            test_id: Test run identifier.
            anomaly_type: Type of anomaly detected.
            severity: Severity level (warning/critical).
        """
        try:
            self._client.put_metric_data(
                Namespace=NAMESPACE,
                MetricData=[
                    {
                        "MetricName": "AnomalyDetected",
                        "Timestamp": datetime.now(timezone.utc),
                        "Value": 1.0,
                        "Unit": "Count",
                        "Dimensions": [
                            {"Name": "TestId", "Value": test_id},
                            {"Name": "AnomalyType", "Value": anomaly_type},
                            {"Name": "Severity", "Value": severity},
                        ],
                    }
                ],
            )
        except ClientError as e:
            logger.warning(f"Failed to publish anomaly metric: {e}")
