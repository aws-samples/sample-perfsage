"""Run the Analysis Agent locally for development/testing.

Usage:
    python -m analysis_agent.local_runner

Uses sample data from tests/sample_data/ to simulate an end-to-end analysis
without needing real S3/DynamoDB resources.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from analysis_agent.agent import create_analysis_agent


SAMPLE_METRICS = {
    "latencies_ms": [
        45, 52, 48, 55, 60, 58, 65, 70, 120, 95,
        88, 92, 150, 200, 180, 250, 310, 400, 500, 450,
        520, 600, 580, 700, 850, 900, 1200, 1500, 1800, 2000,
    ] * 100,
    "rps_over_time": [
        100, 200, 300, 400, 500, 600, 700, 800, 850, 900,
        920, 940, 950, 930, 910, 880, 850, 800, 750, 700,
    ],
    "errors_over_time": [
        0, 0, 0, 0, 0, 1, 2, 5, 8, 15,
        25, 40, 60, 80, 100, 120, 110, 95, 80, 70,
    ],
    "timestamps": list(range(0, 200, 10)),
    "error_codes": {"503": 450, "504": 180, "connection_refused": 70},
}

SAMPLE_SUMMARY = {
    "test_run_id": "run-2026-06-07-demo",
    "total_requests": 30000,
    "failed_requests": 700,
    "duration_seconds": 200,
    "vus_max": 1000,
}

SAMPLE_SLOS = [
    {"name": "P99 Latency", "metric": "latency.p99_ms", "threshold": 500, "operator": "lt"},
    {"name": "Error Rate", "metric": "error_rate", "threshold": 0.01, "operator": "lt"},
    {"name": "Throughput", "metric": "rps_mean", "threshold": 500, "operator": "gte"},
]


def mock_s3_get_object(**kwargs):
    return {"Body": MagicMock(read=lambda: json.dumps(SAMPLE_METRICS).encode())}


def mock_dynamodb_table_get_item(**kwargs):
    return {"Item": SAMPLE_SUMMARY}


def main():
    print("=" * 60)
    print("PerfSage Analysis Agent — Local Development Runner")
    print("=" * 60)
    print()

    with patch("boto3.client") as mock_client, patch("boto3.resource") as mock_resource:
        s3_mock = MagicMock()
        s3_mock.get_object = mock_s3_get_object
        mock_client.return_value = s3_mock

        table_mock = MagicMock()
        table_mock.get_item = mock_dynamodb_table_get_item
        dynamo_mock = MagicMock()
        dynamo_mock.Table.return_value = table_mock
        mock_resource.return_value = dynamo_mock

        agent = create_analysis_agent()

        prompt = (
            "Analyze load test results for run 'run-2026-06-07-demo'. "
            "Raw metrics are in s3://perfsage-results/runs/run-2026-06-07-demo/metrics.json. "
            "Test summary is in DynamoDB table 'perfsage-runs' with key 'run-2026-06-07-demo'. "
            f"Evaluate against these SLOs: {json.dumps(SAMPLE_SLOS)}"
        )

        print(f"Prompt: {prompt}\n")
        print("-" * 60)

        response = agent(prompt)
        print("\n" + "=" * 60)
        print("ANALYSIS REPORT:")
        print("=" * 60)
        print(response)


if __name__ == "__main__":
    main()
