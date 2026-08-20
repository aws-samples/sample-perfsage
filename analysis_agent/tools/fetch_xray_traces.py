"""Fetch AWS X-Ray traces for a completed load-test run and summarize them.

This gives the analysis agent *server-side* evidence (cold starts, per-segment
latency, faults/throttles, downstream timing) instead of inferring root cause
purely from client-side k6 metrics. It requires the target application to have
X-Ray tracing enabled (the sample API does).

The tool derives the test's time window from the run row in DynamoDB
(``started_at`` / ``ended_at``), queries X-Ray for that window (optionally
filtered to the target host), and returns a structured summary.
"""
import datetime as dt
import json
import os

import boto3
from strands import tool

DYNAMODB_TABLE = os.environ.get("PERFSAGE_DYNAMODB_TABLE", "perfsage-test-runs")
REGION = os.environ.get("AWS_REGION", "us-east-1")


@tool
def fetch_xray_traces(test_run_id: str, filter_host: str = "") -> str:
    """Fetch and summarize AWS X-Ray traces for a load-test run's time window.

    Provides server-side evidence to confirm or refute inferred root causes:
    actual Lambda cold-start (Init) durations, per-segment latency (API Gateway,
    Lambda handler, DynamoDB, downstream calls), and fault/error/throttle counts.

    Args:
        test_run_id: The run id (used to look up the test window in DynamoDB).
        filter_host: Optional target host substring (e.g. the API Gateway id)
            to restrict traces to the app under test. Strongly recommended,
            otherwise PerfSage's own agent traces may be included.

    Returns a JSON summary; downstream tools should treat it as ground-truth
    evidence that outranks pattern-matched guesses.
    """
    return json.dumps(summarize_xray_traces(test_run_id, filter_host))


def summarize_xray_traces(test_run_id: str, filter_host: str = "") -> dict:
    """Core X-Ray summary logic returning a dict.

    Shared by the ``fetch_xray_traces`` @tool (agent) and the Lambda handler
    (deterministic SLO evaluation)."""
    xray = boto3.client("xray", region_name=REGION)

    start_dt, end_dt, window_source = _resolve_window(test_run_id)

    filter_expr = f'http.url CONTAINS "{filter_host}"' if filter_host else None

    # 1) Trace summaries over the window (cheap, gives counts + status + timing)
    summaries = _get_trace_summaries(xray, start_dt, end_dt, filter_expr)
    if not summaries:
        return {
            "available": False,
            "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat(),
                       "source": window_source},
            "filter_host": filter_host,
            "message": (
                "No X-Ray traces found for this window. Either tracing is not "
                "enabled on the target app, the filter_host did not match, or "
                "the window is off. Root cause must rely on client-side metrics."
            ),
        }

    faults = sum(1 for s in summaries if s.get("HasFault"))
    errors = sum(1 for s in summaries if s.get("HasError"))
    throttles = sum(1 for s in summaries if s.get("HasThrottle"))
    response_times = sorted(float(s.get("ResponseTime", 0) or 0) * 1000 for s in summaries)

    status_dist = {}
    for s in summaries:
        code = ((s.get("Http") or {}).get("HttpStatus"))
        if code is not None:
            status_dist[str(code)] = status_dist.get(str(code), 0) + 1

    services = sorted({
        sid.get("Name") for s in summaries for sid in (s.get("ServiceIds") or [])
        if sid.get("Name")
    })

    # 2) Pull full traces for the slowest N to localize latency + detect cold starts
    slowest = sorted(summaries, key=lambda s: float(s.get("ResponseTime", 0) or 0), reverse=True)
    sample_ids = [s["Id"] for s in slowest[:10] if s.get("Id")]
    segment_stats, cold_starts, slow_examples = _analyze_traces(xray, sample_ids)

    result = {
        "available": True,
        "window": {"start": start_dt.isoformat(), "end": end_dt.isoformat(),
                   "source": window_source},
        "filter_host": filter_host or "(none - may include PerfSage agent traces)",
        "trace_count": len(summaries),
        "faults": faults,          # 5xx / server errors
        "errors": errors,          # 4xx / client errors
        "throttles": throttles,    # throttled (e.g. 429 / DynamoDB throttle)
        "http_status_distribution": status_dist,
        "response_time_ms": {
            "p50": _pct(response_times, 50),
            "p90": _pct(response_times, 90),
            "p99": _pct(response_times, 99),
            "max": round(response_times[-1], 1) if response_times else 0,
        },
        "services": services,
        "cold_starts": cold_starts,
        "segment_latency_ms": segment_stats,
        "slowest_traces": slow_examples,
        "interpretation_hint": (
            "Use cold_starts.count/avg_init_ms to confirm/deny a cold-start root "
            "cause; use segment_latency_ms to attribute the latency tail to a "
            "specific component (Lambda init vs handler vs DynamoDB vs downstream); "
            "use throttles/faults to confirm rate-limiting or dependency failures."
        ),
    }
    return result


# ── helpers ─────────────────────────────────────────────────────────────────
def _resolve_window(test_run_id: str):
    """Return (start_dt, end_dt, source) for the run, from DynamoDB or a default."""
    try:
        table = boto3.resource("dynamodb", region_name=REGION).Table(DYNAMODB_TABLE)
        item = table.get_item(Key={"test_id": test_run_id}).get("Item", {})
        started = _epoch_s(item.get("started_at"))
        ended = _epoch_s(item.get("ended_at"))
        if started and ended and ended > started:
            # pad by 60s each side to catch trailing traces
            return (
                dt.datetime.fromtimestamp(started - 60, dt.timezone.utc),
                dt.datetime.fromtimestamp(ended + 60, dt.timezone.utc),
                "dynamodb(started_at/ended_at)",
            )
    except Exception:  # noqa: BLE001
        pass
    # Fallback: last 2 hours
    now = dt.datetime.now(dt.timezone.utc)
    return now - dt.timedelta(hours=2), now, "fallback(last 2h)"


def _epoch_s(v):
    """Coerce a DynamoDB epoch value (ms or s) to seconds."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n / 1000.0 if n > 1e12 else n  # values > ~1e12 are milliseconds


def _get_trace_summaries(xray, start_dt, end_dt, filter_expr, max_pages=20):
    summaries = []
    kwargs = {"StartTime": start_dt, "EndTime": end_dt, "TimeRangeType": "Event",
              "Sampling": False}
    if filter_expr:
        kwargs["FilterExpression"] = filter_expr
    token = None
    for _ in range(max_pages):
        if token:
            kwargs["NextToken"] = token
        try:
            resp = xray.get_trace_summaries(**kwargs)
        except Exception:  # noqa: BLE001 - bad filter/expr or perms; return what we have
            break
        summaries.extend(resp.get("TraceSummaries", []))
        token = resp.get("NextToken")
        if not token:
            break
    return summaries


def _analyze_traces(xray, trace_ids):
    """Batch-get full traces and extract cold starts + per-segment latency."""
    segment_durations = {}   # name -> list of ms
    init_durations = []      # cold-start init subsegment durations (ms)
    slow_examples = []

    for chunk in _chunks(trace_ids, 5):
        try:
            resp = xray.batch_get_traces(TraceIds=chunk)
        except Exception:  # noqa: BLE001
            continue
        for trace in resp.get("Traces", []):
            trace_ms = float(trace.get("Duration", 0) or 0) * 1000
            top_url, top_status = None, None
            for seg in trace.get("Segments", []):
                try:
                    doc = json.loads(seg.get("Document", "{}"))
                except json.JSONDecodeError:
                    continue
                _walk_segment(doc, segment_durations, init_durations)
                http = doc.get("http", {})
                req = http.get("request", {})
                resp_ = http.get("response", {})
                if req.get("url") and not top_url:
                    top_url = req.get("url")
                if resp_.get("status") and not top_status:
                    top_status = resp_.get("status")
            slow_examples.append({
                "duration_ms": round(trace_ms, 1),
                "url": top_url,
                "status": top_status,
            })

    segment_stats = {
        name: {
            "count": len(vals),
            "avg_ms": round(sum(vals) / len(vals), 1),
            "max_ms": round(max(vals), 1),
        }
        for name, vals in sorted(segment_durations.items(), key=lambda kv: -max(kv[1]))
        if vals
    }
    cold_starts = {
        "count": len(init_durations),
        "avg_init_ms": round(sum(init_durations) / len(init_durations), 1) if init_durations else 0,
        "max_init_ms": round(max(init_durations), 1) if init_durations else 0,
    }
    return segment_stats, cold_starts, slow_examples[:10]


def _walk_segment(doc, segment_durations, init_durations, depth=0):
    """Recurse a segment/subsegment tree, recording durations and cold starts."""
    name = doc.get("name", "unknown")
    start, end = doc.get("start_time"), doc.get("end_time")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        ms = (end - start) * 1000
        # An "Initialization" subsegment on a Lambda segment == cold start
        if name.lower() in ("initialization", "init"):
            init_durations.append(ms)
        else:
            segment_durations.setdefault(name, []).append(ms)
    for sub in doc.get("subsegments", []) or []:
        _walk_segment(sub, segment_durations, init_durations, depth + 1)


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _pct(sorted_vals, pct):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return round(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac, 1)
