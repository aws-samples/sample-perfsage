"""Ingest k6 load test results from S3 + run metadata from DynamoDB.

The Executor writes per-run artifacts under ``s3://<bucket>/runs/<test_id>/``:

* ``summary.json`` — k6 ``--summary-export`` aggregates (p50/p90/p95/avg/max,
  http_reqs count + rate, http_req_failed rate). Preferred source of truth for
  percentiles and totals because k6 has already aggregated them.
* ``metrics.json`` — k6 ``--out json`` NDJSON time-series (one JSON object per
  line). Used for degradation-point and tail-latency analysis.

DynamoDB row (``perfsage-test-runs``, partition key ``test_id``) provides
run-level metadata (``duration_seconds``, ``vus_max``). If the row is empty
(currently the case — Executor does not always backfill the summary into DDB),
we still complete the analysis using S3 alone and derive duration from the
``started_at`` / ``ended_at`` timestamps if present.
"""
import json
import logging
from collections import defaultdict

import boto3
from strands import tool

logger = logging.getLogger(__name__)


@tool
def ingest_results(s3_bucket: str, s3_key: str, dynamodb_table: str, test_run_id: str) -> str:
    """Ingest raw load test metrics from S3 and DynamoDB.

    Reads ``summary.json`` (k6 summary-export) for aggregates and
    ``metrics.json`` (k6 NDJSON) for time-series, then merges run-level metadata
    from DynamoDB. Returns a consolidated metrics payload for downstream tools.
    """
    return json.dumps(ingest_metrics(s3_bucket, s3_key, dynamodb_table, test_run_id))


def ingest_metrics(s3_bucket: str, s3_key: str, dynamodb_table: str, test_run_id: str) -> dict:
    """Core ingest logic returning a consolidated metrics dict.

    Shared by the ``ingest_results`` @tool (for the agent) and the Lambda handler
    (for deterministic SLO evaluation). On a missing summary.json it returns a
    dict with an ``error`` key.
    """
    s3 = boto3.client("s3")
    dynamodb = boto3.resource("dynamodb")

    # Run-level metadata from DynamoDB (best-effort; row may be empty)
    run_summary = {}
    try:
        table = dynamodb.Table(dynamodb_table)
        response = table.get_item(Key={"test_id": test_run_id})
        run_summary = response.get("Item", {})
    except Exception as e:  # noqa: BLE001 - keep going on S3 if DDB is unreachable
        logger.warning("DynamoDB lookup failed for %s: %s", test_run_id, e)

    # Derive the run's S3 prefix from the provided metrics key.
    # e.g. "runs/run-abc/metrics.json" -> "runs/run-abc/"
    prefix = s3_key.rsplit("/", 1)[0] + "/" if "/" in s3_key else ""

    # Aggregates from summary.json (required)
    summary_data = _load_json(s3, s3_bucket, f"{prefix}summary.json")
    if summary_data is None:
        return {
            "error": (
                f"summary.json not found at s3://{s3_bucket}/{prefix}summary.json. "
                "The Executor should write k6 --summary-export to this location."
            )
        }

    k6_metrics = summary_data.get("metrics", {}) or {}
    duration_metric = k6_metrics.get("http_req_duration", {}) or {}
    failed_metric = k6_metrics.get("http_req_failed", {}) or {}
    reqs_metric = k6_metrics.get("http_reqs", {}) or {}
    vus_metric = k6_metrics.get("vus", {}) or {}

    # k6 default summaryTrendStats omits p(99); fall back to max so the report
    # still reflects worst-case tail rather than reporting 0.
    p99 = _to_float(duration_metric.get("p(99)")) or _to_float(duration_metric.get("max")) or 0.0

    total_requests = int(_to_float(reqs_metric.get("count")) or 0)
    # http_req_failed is a Rate metric: "passes" = times the rate-tracked
    # condition (request failed) was true, i.e. the failure count.
    failed_requests = int(_to_float(failed_metric.get("passes")) or 0)
    error_rate = _to_float(failed_metric.get("value")) or 0.0
    rps_mean = _to_float(reqs_metric.get("rate")) or 0.0

    # Time-series from metrics.json (best-effort; report still useful without it)
    series = _parse_metrics_ndjson(s3, s3_bucket, f"{prefix}metrics.json")
    latency_over_time = series["latencies"]
    rps_over_time = series["rps_buckets"]
    errors_over_time = series["error_buckets"]
    error_codes = series["error_codes"]

    rps_max = float(max(rps_over_time)) if rps_over_time else rps_mean

    duration_seconds = (
        _to_float(run_summary.get("duration_seconds"))
        or _derive_duration_from_timestamps(run_summary)
        or 0.0
    )
    vus_max = int(
        _to_float(run_summary.get("vus_max"))
        or _to_float(vus_metric.get("max"))
        or 0
    )

    consolidated = {
        "test_run_id": test_run_id,
        "total_requests": total_requests,
        "successful_requests": total_requests - failed_requests,
        "failed_requests": failed_requests,
        "rps_mean": rps_mean,
        "rps_max": rps_max,
        "latency": {
            "p50_ms": _to_float(duration_metric.get("med")) or 0.0,
            "p90_ms": _to_float(duration_metric.get("p(90)")) or 0.0,
            "p95_ms": _to_float(duration_metric.get("p(95)")) or 0.0,
            "p99_ms": float(p99),
            "mean_ms": _to_float(duration_metric.get("avg")) or 0.0,
            "max_ms": _to_float(duration_metric.get("max")) or 0.0,
        },
        "error_rate": float(error_rate),
        "duration_seconds": float(duration_seconds),
        "vus_max": int(vus_max),
        "latency_over_time": latency_over_time,
        "rps_over_time": rps_over_time,
        "errors_over_time": errors_over_time,
        "error_codes": error_codes,
    }

    return consolidated


# ── helpers ────────────────────────────────────────────────────────────────
def _load_json(s3, bucket: str, key: str):
    """Fetch an S3 JSON object. Returns None if missing or unreadable."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - any S3/JSON failure means "not available"
        logger.info("Could not load s3://%s/%s: %s", bucket, key, e)
        return None


def _parse_metrics_ndjson(s3, bucket: str, key: str) -> dict:
    """Parse k6's --out json NDJSON. Best-effort: returns empty time-series on failure.

    Each line is one JSON object. We care about ``Point`` records:
      - ``http_req_duration``: append value (ms) to latency series
      - ``http_reqs``: bucket counts per ISO-second to form rps series
      - ``http_req_failed`` where value == 1: bucket per ISO-second to form
        errors series, and tally ``tags.status`` into error_codes
    """
    empty = {"latencies": [], "rps_buckets": [], "error_buckets": [], "error_codes": {}}
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception as e:  # noqa: BLE001
        logger.info("metrics.json unavailable for %s/%s (skipping time-series): %s",
                    bucket, key, e)
        return empty

    latencies: list[float] = []
    rps_per_sec: dict[str, int] = defaultdict(int)
    err_per_sec: dict[str, int] = defaultdict(int)
    codes: dict[str, int] = defaultdict(int)

    for line in body.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "Point":
            continue

        metric = record.get("metric")
        data = record.get("data") or {}
        value = data.get("value")
        tags = data.get("tags") or {}
        time_str = data.get("time") or ""
        sec_bucket = time_str[:19]  # truncate to second precision for bucketing

        if metric == "http_req_duration" and value is not None:
            try:
                latencies.append(float(value))
            except (TypeError, ValueError):
                pass
        elif metric == "http_reqs":
            try:
                rps_per_sec[sec_bucket] += int(value or 0)
            except (TypeError, ValueError):
                pass
            # Tally non-2xx status codes for error_codes attribution
            if tags.get("expected_response") == "false":
                status = tags.get("status")
                if status:
                    codes[str(status)] += 1
        elif metric == "http_req_failed":
            try:
                if int(value or 0) == 1:
                    err_per_sec[sec_bucket] += 1
            except (TypeError, ValueError):
                pass

    return {
        "latencies": latencies,
        "rps_buckets": [v for _, v in sorted(rps_per_sec.items())],
        "error_buckets": [v for _, v in sorted(err_per_sec.items())],
        "error_codes": dict(codes),
    }


def _to_float(value):
    """Best-effort numeric coercion. DynamoDB returns Decimal; k6 returns float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _derive_duration_from_timestamps(run_summary: dict) -> float:
    """If duration_seconds isn't stored, compute it from started_at/ended_at ms."""
    started = _to_float(run_summary.get("started_at"))
    ended = _to_float(run_summary.get("ended_at"))
    if started and ended and ended > started:
        return (ended - started) / 1000.0
    return 0.0
