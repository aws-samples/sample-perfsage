"""Unit tests for k6 output parser."""

import json
from pathlib import Path

import pytest

from perfsage_executor.utils.k6_parser import K6OutputParser


@pytest.fixture
def parser():
    return K6OutputParser(test_id="test-001")


@pytest.fixture
def sample_output():
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    return (fixtures_dir / "sample_k6_output.json").read_text().strip().splitlines()


class TestK6OutputParser:
    def test_parse_valid_point(self, parser):
        line = json.dumps({
            "type": "Point",
            "data": {
                "time": "2026-06-08T10:00:01.000Z",
                "value": 45.23,
                "tags": {"url": "http://localhost/get", "method": "GET", "status": "200"},
            },
            "metric": "http_req_duration",
        })
        point = parser.parse_line(line)
        assert point is not None
        assert point.metric_name == "http_req_duration"
        assert point.value == 45.23
        assert point.tags["method"] == "GET"

    def test_parse_non_point_type(self, parser):
        line = json.dumps({"type": "Metric", "data": {"value": 1}, "metric": "http_reqs"})
        point = parser.parse_line(line)
        assert point is None

    def test_parse_invalid_json(self, parser):
        point = parser.parse_line("not valid json")
        assert point is None

    def test_parse_empty_line(self, parser):
        point = parser.parse_line("")
        assert point is None

    def test_accumulates_requests(self, parser):
        for i in range(5):
            parser.parse_line(json.dumps({
                "type": "Point",
                "data": {"time": "2026-06-08T10:00:01.000Z", "value": 1, "tags": {}},
                "metric": "http_reqs",
            }))
        snapshot = parser.get_snapshot()
        assert snapshot.total_requests == 5

    def test_accumulates_errors(self, parser):
        # 3 requests, 1 failure
        for _ in range(3):
            parser.parse_line(json.dumps({
                "type": "Point",
                "data": {"time": "2026-06-08T10:00:01.000Z", "value": 1, "tags": {}},
                "metric": "http_reqs",
            }))
        parser.parse_line(json.dumps({
            "type": "Point",
            "data": {"time": "2026-06-08T10:00:01.000Z", "value": 1, "tags": {}},
            "metric": "http_req_failed",
        }))
        snapshot = parser.get_snapshot()
        assert snapshot.total_errors == 1
        assert snapshot.error_rate_pct == pytest.approx(33.33, rel=0.01)

    def test_tracks_vus(self, parser):
        parser.parse_line(json.dumps({
            "type": "Point",
            "data": {"time": "2026-06-08T10:00:01.000Z", "value": 50, "tags": {}},
            "metric": "vus",
        }))
        snapshot = parser.get_snapshot()
        assert snapshot.active_vus == 50

    def test_latency_percentiles(self, parser):
        # Add 100 latency values
        for i in range(100):
            parser.parse_line(json.dumps({
                "type": "Point",
                "data": {"time": "2026-06-08T10:00:01.000Z", "value": float(i + 1), "tags": {}},
                "metric": "http_req_duration",
            }))
        snapshot = parser.get_snapshot()
        assert snapshot.latency_p50_ms == 51.0
        assert snapshot.latency_p90_ms == 91.0
        assert snapshot.latency_p99_ms == 100.0

    def test_snapshot_resets_window(self, parser):
        parser.parse_line(json.dumps({
            "type": "Point",
            "data": {"time": "2026-06-08T10:00:01.000Z", "value": 1, "tags": {}},
            "metric": "http_reqs",
        }))
        snapshot1 = parser.get_snapshot()
        assert snapshot1.total_requests == 1

        # Window counter should reset but total stays
        snapshot2 = parser.get_snapshot()
        assert snapshot2.rps == 0.0
        # Total stays cumulative
        assert snapshot2.total_requests == 1

    def test_parse_sample_fixture(self, parser, sample_output):
        for line in sample_output:
            parser.parse_line(line)
        snapshot = parser.get_snapshot()
        assert snapshot.total_requests == 4
        assert snapshot.total_errors == 1
        assert snapshot.active_vus == 20

    def test_parse_summary_json(self, parser):
        summary = {
            "metrics": {
                "http_req_duration": {
                    "values": {"avg": 120.5, "med": 95.0, "p(90)": 200.0, "p(95)": 300.0, "p(99)": 500.0, "max": 800.0}
                },
                "http_reqs": {"values": {"count": 10000, "rate": 333.3}},
                "http_req_failed": {"values": {"passes": 50}},
            }
        }
        result = parser.parse_summary(json.dumps(summary))
        assert result["avg_latency_ms"] == 120.5
        assert result["p99_latency_ms"] == 500.0
        assert result["total_requests"] == 10000
        assert result["avg_rps"] == 333.3
        assert result["total_errors"] == 50
