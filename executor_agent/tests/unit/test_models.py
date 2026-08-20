"""Unit tests for data models."""

import json

import pytest

from perfsage_executor.models.test_config import ExecutionMode, TestConfig, ThresholdConfig
from perfsage_executor.models.test_run import InfraDetails, TestRun, TestStatus, TestSummary
from perfsage_executor.models.metrics import AnomalyEvent, AnomalySeverity, AnomalyType, MetricSnapshot


class TestTestConfig:
    def test_creates_with_defaults(self):
        config = TestConfig(
            script_path="/tmp/test.js",
            virtual_users=100,
            duration="5m",
        )
        assert config.test_id.startswith("run-")
        assert config.execution_mode == ExecutionMode.LOCAL
        assert config.ramp_up == "0s"
        assert config.auto_stop_on_anomaly is True

    def test_validates_vus_range(self):
        with pytest.raises(Exception):
            TestConfig(script_path="/tmp/test.js", virtual_users=0, duration="5m")
        with pytest.raises(Exception):
            TestConfig(script_path="/tmp/test.js", virtual_users=10001, duration="5m")

    def test_to_k6_env(self):
        config = TestConfig(
            script_path="/tmp/test.js",
            virtual_users=200,
            duration="10m",
            environment_vars={"TARGET_URL": "http://api.example.com"},
        )
        env = config.to_k6_env()
        assert env["K6_VUS"] == "200"
        assert env["K6_DURATION"] == "10m"
        assert env["TARGET_URL"] == "http://api.example.com"
        assert "PERFSAGE_TEST_ID" in env

    def test_serialization_roundtrip(self):
        config = TestConfig(
            script_path="/tmp/test.js",
            virtual_users=50,
            duration="2m",
            execution_mode=ExecutionMode.FARGATE,
        )
        json_str = config.model_dump_json()
        restored = TestConfig.model_validate_json(json_str)
        assert restored.virtual_users == 50
        assert restored.execution_mode == ExecutionMode.FARGATE


class TestTestRun:
    def test_lifecycle_transitions(self):
        run = TestRun(test_id="run-001")
        assert run.status == TestStatus.PENDING

        infra = InfraDetails(execution_mode="local", container_id="abc123")
        run.mark_provisioning(infra)
        assert run.status == TestStatus.PROVISIONING

        run.mark_running()
        assert run.status == TestStatus.RUNNING

        summary = TestSummary(total_requests=1000, error_rate_pct=0.5)
        run.mark_completed(summary, "s3://bucket/runs/run-001/")
        assert run.status == TestStatus.COMPLETED
        assert run.ended_at is not None

    def test_mark_failed(self):
        run = TestRun(test_id="run-002")
        run.mark_failed("Container crashed")
        assert run.status == TestStatus.FAILED
        assert run.error_message == "Container crashed"

    def test_to_dynamodb_item(self):
        run = TestRun(test_id="run-003")
        item = run.to_dynamodb_item()
        assert item["test_id"] == "run-003"
        assert item["status"] == "pending"
        assert "started_at" in item

    def test_add_anomaly(self):
        run = TestRun(test_id="run-004")
        anomaly = AnomalyEvent(
            severity=AnomalySeverity.CRITICAL,
            anomaly_type=AnomalyType.SPIKE,
            description="Latency spike detected",
            metric_name="latency_p99_ms",
            current_value=800.0,
            expected_range=(0, 500.0),
        )
        run.add_anomaly(anomaly)
        assert len(run.anomalies) == 1


class TestMetricSnapshot:
    def test_to_websocket_message(self):
        snapshot = MetricSnapshot(
            test_id="run-005",
            rps=1250.5,
            latency_p50_ms=45.0,
            latency_p90_ms=120.0,
            latency_p95_ms=180.0,
            latency_p99_ms=340.0,
            error_rate_pct=0.3,
            active_vus=500,
            total_requests=75000,
            total_errors=225,
        )
        msg = snapshot.to_websocket_message()
        assert msg["test_id"] == "run-005"
        assert msg["metrics"]["rps"] == 1250.5
        assert msg["metrics"]["active_vus"] == 500
        assert "timestamp" in msg
