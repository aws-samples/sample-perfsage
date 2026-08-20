"""Unit tests for Analysis Agent tools.

Tests the core logic of each tool independently without AWS calls or LLM invocation.
"""

import json

import numpy as np
import pytest

from analysis_agent.tools.analyze_metrics import analyze_metrics
from analysis_agent.tools.evaluate_slos import evaluate_slos
from analysis_agent.tools.generate_recommendations import generate_recommendations
from analysis_agent.tools.identify_root_cause import identify_root_cause


def _make_metrics_json(
    latencies=None,
    rps=None,
    errors=None,
    error_codes=None,
    total_requests=1000,
    failed_requests=50,
):
    if latencies is None:
        latencies = list(np.random.exponential(100, 500).astype(float))
    if rps is None:
        rps = [100.0] * 20
    if errors is None:
        errors = [0] * 20
    if error_codes is None:
        error_codes = {}

    latencies_arr = np.array(latencies)

    return json.dumps({
        "test_run_id": "test-run-1",
        "total_requests": total_requests,
        "successful_requests": total_requests - failed_requests,
        "failed_requests": failed_requests,
        "rps_mean": float(np.mean(rps)),
        "rps_max": float(np.max(rps)),
        "latency": {
            "p50_ms": float(np.percentile(latencies_arr, 50)),
            "p90_ms": float(np.percentile(latencies_arr, 90)),
            "p99_ms": float(np.percentile(latencies_arr, 99)),
            "mean_ms": float(np.mean(latencies_arr)),
            "max_ms": float(np.max(latencies_arr)),
        },
        "error_rate": failed_requests / total_requests,
        "duration_seconds": 120,
        "vus_max": 500,
        "timestamps": list(range(len(rps))),
        "latency_over_time": [float(x) for x in latencies],
        "rps_over_time": [float(x) for x in rps],
        "errors_over_time": errors,
        "error_codes": error_codes,
    })


class TestAnalyzeMetrics:
    def test_basic_analysis(self):
        metrics_json = _make_metrics_json()
        result = json.loads(analyze_metrics(metrics_json=metrics_json))
        assert "latency_analysis" in result
        assert "throughput_analysis" in result
        assert "error_analysis" in result
        assert "degradation_point" in result

    def test_detects_degradation(self):
        latencies = [50.0] * 200 + [500.0] * 100 + [2000.0] * 200
        metrics_json = _make_metrics_json(latencies=latencies)
        result = json.loads(analyze_metrics(metrics_json=metrics_json))
        assert result["degradation_point"]["detected"] is True

    def test_stable_latency(self):
        latencies = [100 + np.random.normal(0, 5) for _ in range(500)]
        metrics_json = _make_metrics_json(latencies=latencies)
        result = json.loads(analyze_metrics(metrics_json=metrics_json))
        assert result["latency_analysis"]["stability"] == "stable"


class TestIdentifyRootCause:
    def test_connection_pool_exhaustion(self):
        analysis = {
            "latency_analysis": {"tail_severity": "extreme_tail", "stability": "high_variance"},
            "throughput_analysis": {"trend": "declining"},
            "error_analysis": {
                "error_distribution": {"503": 200, "connection_refused": 50},
                "error_concentration": "late",
                "burst_detected": False,
            },
            "degradation_point": {"detected": True},
        }
        result = json.loads(identify_root_cause(analysis_json=json.dumps(analysis)))
        patterns = [rc["pattern"] for rc in result["root_causes"]]
        assert "connection_pool_exhaustion" in patterns

    def test_rate_limiting(self):
        analysis = {
            "latency_analysis": {"tail_severity": "normal", "stability": "stable"},
            "throughput_analysis": {"trend": "stable"},
            "error_analysis": {
                "error_distribution": {"429": 300},
                "error_concentration": "distributed",
                "burst_detected": False,
            },
            "degradation_point": {"detected": False},
        }
        result = json.loads(identify_root_cause(analysis_json=json.dumps(analysis)))
        patterns = [rc["pattern"] for rc in result["root_causes"]]
        assert "rate_limiting" in patterns


class TestEvaluateSLOs:
    def test_all_pass(self):
        metrics_json = _make_metrics_json(
            latencies=[50.0] * 500,
            failed_requests=5,
            total_requests=1000,
        )
        slos = json.dumps([
            {"name": "P99 Latency", "metric": "latency.p99_ms", "threshold": 500, "operator": "lt"},
            {"name": "Error Rate", "metric": "error_rate", "threshold": 0.01, "operator": "lt"},
        ])
        result = json.loads(evaluate_slos(metrics_json=metrics_json, slo_definitions_json=slos))
        assert result["overall_verdict"] == "pass"
        assert result["passed_count"] == 2

    def test_slo_failure(self):
        metrics_json = _make_metrics_json(
            latencies=[800.0] * 500,
            failed_requests=200,
            total_requests=1000,
        )
        slos = json.dumps([
            {"name": "P99 Latency", "metric": "latency.p99_ms", "threshold": 500, "operator": "lt"},
            {"name": "Error Rate", "metric": "error_rate", "threshold": 0.01, "operator": "lt"},
        ])
        result = json.loads(evaluate_slos(metrics_json=metrics_json, slo_definitions_json=slos))
        assert result["overall_verdict"] == "fail"
        assert result["failed_count"] == 2


class TestGenerateRecommendations:
    def test_recommendations_for_pool_exhaustion(self):
        root_causes = json.dumps({
            "root_causes": [
                {"pattern": "connection_pool_exhaustion", "confidence": "high", "match_score": 0.85}
            ]
        })
        analysis = json.dumps({
            "summary_stats": {"error_rate_percent": 8},
            "latency_analysis": {"distribution": {"tail_ratio_p99_p50": 15}},
            "degradation_point": {"detected": True, "at_percent_through_test": 40},
        })
        result = json.loads(generate_recommendations(
            root_cause_json=root_causes, analysis_json=analysis
        ))
        titles = [r["title"] for r in result["recommendations"]]
        assert "Increase connection pool size" in titles
        assert result["recommendations"][0]["priority"] == "critical"

    def test_declining_throughput_triggers_autoscaling_rec(self):
        root_causes = json.dumps({"root_causes": []})
        analysis = json.dumps({
            "summary_stats": {"error_rate_percent": 1, "rps_mean": 100},
            "latency_analysis": {"distribution": {"tail_ratio_p99_p50": 2}},
            "throughput_analysis": {"trend": "declining", "max_rps": 100, "sustained_capacity": 90},
            "degradation_point": {"detected": False},
        })
        result = json.loads(generate_recommendations(
            root_cause_json=root_causes, analysis_json=analysis
        ))
        recs = {r["title"]: r for r in result["recommendations"]}
        assert "Tune autoscaling to react before throughput collapses" in recs
        assert recs["Tune autoscaling to react before throughput collapses"]["category"] == "infrastructure"

    def test_capacity_gap_triggers_capacity_rec(self):
        root_causes = json.dumps({"root_causes": []})
        analysis = json.dumps({
            "summary_stats": {"error_rate_percent": 1, "rps_mean": 100},
            "latency_analysis": {"distribution": {"tail_ratio_p99_p50": 2}},
            "throughput_analysis": {"trend": "stable", "max_rps": 200, "sustained_capacity": 80},
            "degradation_point": {"detected": False},
        })
        result = json.loads(generate_recommendations(
            root_cause_json=root_causes, analysis_json=analysis
        ))
        titles = [r["title"] for r in result["recommendations"]]
        assert "Sustained capacity is far below peak throughput" in titles

    def test_heavy_tail_triggers_caching_rec(self):
        root_causes = json.dumps({"root_causes": []})
        analysis = json.dumps({
            "summary_stats": {"error_rate_percent": 1, "rps_mean": 100},
            "latency_analysis": {"distribution": {"tail_ratio_p99_p50": 8}},
            "throughput_analysis": {"trend": "stable", "max_rps": 100, "sustained_capacity": 95},
            "degradation_point": {"detected": False},
        })
        result = json.loads(generate_recommendations(
            root_cause_json=root_causes, analysis_json=analysis
        ))
        titles = [r["title"] for r in result["recommendations"]]
        assert "Add a CDN or caching layer for read-heavy traffic" in titles

    def test_stable_run_emits_no_infra_general_recs(self):
        root_causes = json.dumps({"root_causes": []})
        analysis = json.dumps({
            "summary_stats": {"error_rate_percent": 1, "rps_mean": 100},
            "latency_analysis": {"distribution": {"tail_ratio_p99_p50": 2}},
            "throughput_analysis": {"trend": "stable", "max_rps": 100, "sustained_capacity": 95},
            "degradation_point": {"detected": False},
        })
        result = json.loads(generate_recommendations(
            root_cause_json=root_causes, analysis_json=analysis
        ))
        assert result["recommendations"] == []
