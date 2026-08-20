import json

from strands import tool


REMEDIATION_MAP = {
    "connection_pool_exhaustion": [
        {
            "title": "Increase connection pool size",
            "description": "The connection pool is exhausted under load. Increase the max pool size to match your peak concurrent request count. Monitor pool utilization to find the right size.",
            "priority": "critical",
            "category": "configuration",
        },
        {
            "title": "Add connection timeout with retry",
            "description": "Set a connection acquisition timeout (e.g., 5s) with exponential backoff retry to prevent requests from hanging indefinitely when the pool is full.",
            "priority": "high",
            "category": "code",
        },
        {
            "title": "Implement circuit breaker",
            "description": "Add a circuit breaker to fail fast when the pool is consistently exhausted, preventing cascading failures to upstream callers.",
            "priority": "medium",
            "category": "architecture",
        },
        {
            "title": "Enable connection draining on the load balancer",
            "description": "Configure connection draining (deregistration delay) on the load balancer so in-flight requests complete during scale-in or deploys, and reuse keep-alive connections to reduce churn against the backend pool.",
            "priority": "medium",
            "category": "infrastructure",
        },
    ],
    "memory_pressure": [
        {
            "title": "Increase instance memory or scale horizontally",
            "description": "Service is hitting memory limits under load. Either allocate more memory per instance or add more instances to distribute the load.",
            "priority": "critical",
            "category": "infrastructure",
        },
        {
            "title": "Right-size to a memory-optimized instance type",
            "description": "The workload is memory-bound. Move to a memory-optimized instance family (more RAM per vCPU) rather than scaling CPU, which is cheaper than over-provisioning general-purpose instances to hit a memory target.",
            "priority": "high",
            "category": "infrastructure",
        },
        {
            "title": "Profile memory allocations under load",
            "description": "Run a heap profile during a load test to identify the largest allocators. Look for unbounded caches, leaked connections, or large response buffering.",
            "priority": "high",
            "category": "code",
        },
    ],
    "thread_starvation": [
        {
            "title": "Increase worker/thread pool size",
            "description": "All workers are blocked. Increase the thread pool to handle concurrent requests, or switch blocking I/O calls to async.",
            "priority": "critical",
            "category": "configuration",
        },
        {
            "title": "Right-size to a compute-optimized instance type",
            "description": "If workers are CPU-bound rather than I/O-bound, move to a compute-optimized instance family (more vCPU per instance) so the larger thread pool actually has cores to run on.",
            "priority": "medium",
            "category": "infrastructure",
        },
        {
            "title": "Add async I/O for downstream calls",
            "description": "Convert synchronous downstream HTTP/DB calls to async to free worker threads while waiting on I/O.",
            "priority": "high",
            "category": "code",
        },
    ],
    "rate_limiting": [
        {
            "title": "Request rate limit increase from dependency",
            "description": "Your traffic exceeds the rate limit of a dependency. Request a higher quota or implement request queuing with backpressure.",
            "priority": "high",
            "category": "infrastructure",
        },
        {
            "title": "Add client-side rate limiter",
            "description": "Proactively limit outgoing request rate just below the threshold to avoid 429s and maintain predictable throughput.",
            "priority": "medium",
            "category": "code",
        },
    ],
    "cold_start_penalty": [
        {
            "title": "Pre-warm instances before load test",
            "description": "Send a warm-up traffic phase (low RPS for 1-2 minutes) before ramping to target load, allowing JIT compilation and connection establishment.",
            "priority": "high",
            "category": "configuration",
        },
        {
            "title": "Use provisioned concurrency (Lambda) or min instances",
            "description": "Keep a minimum number of warm instances ready to eliminate cold start latency for initial requests.",
            "priority": "medium",
            "category": "infrastructure",
        },
    ],
    "downstream_timeout": [
        {
            "title": "Reduce downstream timeout and add fallback",
            "description": "The current timeout allows requests to hang too long. Reduce it and implement a fallback (cached response, degraded mode) when the dependency is unresponsive.",
            "priority": "critical",
            "category": "code",
        },
        {
            "title": "Add bulkhead isolation for downstream calls",
            "description": "Isolate downstream calls into separate thread/connection pools so one slow dependency doesn't block all request processing.",
            "priority": "high",
            "category": "architecture",
        },
        {
            "title": "Offload read traffic to database read replicas",
            "description": "If the unresponsive dependency is a database, add read replicas and route read-only queries to them to relieve the primary. For write-bound or very large tables, consider sharding/partitioning to spread load across nodes.",
            "priority": "medium",
            "category": "infrastructure",
        },
    ],
}


@tool
def generate_recommendations(root_cause_json: str, analysis_json: str) -> str:
    """Generate specific, actionable recommendations based on identified root causes and metrics analysis.

    Maps each root cause to concrete remediation steps with priority levels and categories.
    Also generates general performance recommendations based on the observed metrics patterns.
    """
    root_causes = json.loads(root_cause_json)
    analysis = json.loads(analysis_json)

    recommendations = []
    seen_titles = set()

    for rc in root_causes.get("root_causes", []):
        pattern = rc["pattern"]
        if pattern in REMEDIATION_MAP:
            for rec in REMEDIATION_MAP[pattern]:
                if rec["title"] not in seen_titles:
                    recommendations.append({
                        **rec,
                        "triggered_by": pattern,
                        "confidence": rc["confidence"],
                    })
                    seen_titles.add(rec["title"])

    general_recs = _general_recommendations(analysis)
    for rec in general_recs:
        if rec["title"] not in seen_titles:
            recommendations.append(rec)
            seen_titles.add(rec["title"])

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    recommendations.sort(key=lambda x: priority_order.get(x["priority"], 4))

    return json.dumps({"recommendations": recommendations})


def _general_recommendations(analysis: dict) -> list:
    recs = []
    summary = analysis.get("summary_stats", {})
    latency_analysis = analysis.get("latency_analysis", {})
    throughput = analysis.get("throughput_analysis", {})
    degradation = analysis.get("degradation_point", {})

    if summary.get("error_rate_percent", 0) > 5:
        recs.append({
            "title": "Investigate high error rate",
            "description": f"Error rate of {summary['error_rate_percent']}% exceeds typical thresholds. Review error logs at peak load to identify failing endpoints or dependencies.",
            "priority": "critical",
            "category": "code",
            "triggered_by": "general_analysis",
        })

    dist = latency_analysis.get("distribution", {})
    if dist.get("tail_ratio_p99_p50", 0) > 10:
        recs.append({
            "title": "Investigate extreme latency outliers",
            "description": f"p99/p50 ratio of {dist['tail_ratio_p99_p50']}x indicates severe outliers. Profile the slowest 1% of requests to find GC pauses, lock contention, or slow queries.",
            "priority": "high",
            "category": "code",
            "triggered_by": "general_analysis",
        })

    if degradation.get("detected") and degradation.get("at_percent_through_test", 100) < 50:
        recs.append({
            "title": "Service degrades too early in load ramp",
            "description": f"Performance degraded at {degradation['at_percent_through_test']}% through the test ({degradation.get('rps_at_degradation', 0)} RPS). The service cannot sustain the target load — consider scaling or optimizing the hot path.",
            "priority": "high",
            "category": "infrastructure",
            "triggered_by": "general_analysis",
        })

    if throughput.get("trend") == "declining":
        recs.append({
            "title": "Tune autoscaling to react before throughput collapses",
            "description": "Throughput declined as load increased, suggesting the fleet did not scale out in time. Switch to a target-tracking autoscaling policy (e.g., target CPU or request count per target), lower the scale-out threshold, and shorten the cooldown so capacity is added before saturation rather than after.",
            "priority": "high",
            "category": "infrastructure",
            "triggered_by": "general_analysis",
        })

    sustained = throughput.get("sustained_capacity")
    peak = throughput.get("max_rps")
    if sustained is not None and peak and sustained < peak * 0.5:
        recs.append({
            "title": "Sustained capacity is far below peak throughput",
            "description": f"Sustained capacity ({sustained} RPS) is less than half of peak throughput ({peak} RPS), indicating the fleet cannot hold peak load. Provision for the sustained target with autoscaling headroom, and verify per-instance network/ENI bandwidth and connection limits are not capping throughput before CPU/memory do.",
            "priority": "medium",
            "category": "infrastructure",
            "triggered_by": "general_analysis",
        })

    dist = latency_analysis.get("distribution", {})
    if dist.get("tail_ratio_p99_p50", 0) > 5 and summary.get("rps_mean", 0) > 0:
        recs.append({
            "title": "Add a CDN or caching layer for read-heavy traffic",
            "description": "A heavy latency tail under sustained throughput often points to repeated expensive reads. Put a CDN in front of cacheable responses and add an in-memory/distributed cache (e.g., for hot keys and idempotent GETs) to cut origin load and trim tail latency.",
            "priority": "medium",
            "category": "infrastructure",
            "triggered_by": "general_analysis",
        })

    return recs
