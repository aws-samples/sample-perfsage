import json

from strands import tool


KNOWN_FAILURE_PATTERNS = [
    {
        "pattern": "connection_pool_exhaustion",
        "indicators": {
            "error_codes": ["503", "connection_refused", "ECONNRESET"],
            "latency_behavior": "extreme_tail",
            "error_concentration": "late",
            "degradation_detected": True,
        },
        "description": "Connection pool exhaustion — backend ran out of available connections under high concurrency",
    },
    {
        "pattern": "memory_pressure",
        "indicators": {
            "error_codes": ["502", "503"],
            "latency_behavior": "high_variance",
            "throughput_trend": "declining",
            "degradation_detected": True,
        },
        "description": "Memory pressure — service memory grew until GC pauses or OOM caused degradation",
    },
    {
        "pattern": "thread_starvation",
        "indicators": {
            "error_codes": ["504", "timeout"],
            "latency_behavior": "heavy_tail",
            "error_concentration": "late",
            "throughput_trend": "declining",
        },
        "description": "Thread/worker starvation — all workers blocked waiting on downstream or I/O",
    },
    {
        "pattern": "rate_limiting",
        "indicators": {
            "error_codes": ["429", "throttled"],
            "error_concentration": "distributed",
            "throughput_trend": "stable",
        },
        "description": "Rate limiting — upstream or downstream throttled requests once a threshold was hit",
    },
    {
        "pattern": "cold_start_penalty",
        "indicators": {
            "error_concentration": "early",
            "latency_behavior": "high_variance",
        },
        "description": "Cold start penalty — initial requests suffered high latency before instances warmed up",
    },
    {
        "pattern": "downstream_timeout",
        "indicators": {
            "error_codes": ["504", "timeout", "ETIMEDOUT"],
            "latency_behavior": "extreme_tail",
            "burst_detected": True,
        },
        "description": "Downstream timeout — a dependency became unresponsive causing cascading failures",
    },
]


@tool
def identify_root_cause(analysis_json: str) -> str:
    """Identify probable root causes by correlating error patterns with latency behavior.

    Matches observed metrics patterns against known failure modes (connection pool exhaustion,
    memory pressure, thread starvation, rate limiting, cold starts, downstream timeouts).
    Returns ranked root causes with confidence levels and supporting evidence.
    """
    analysis = json.loads(analysis_json)

    error_analysis = analysis.get("error_analysis", {})
    latency_analysis = analysis.get("latency_analysis", {})
    throughput_analysis = analysis.get("throughput_analysis", {})
    degradation = analysis.get("degradation_point", {})

    observed = {
        "error_codes": list(error_analysis.get("error_distribution", {}).keys()),
        "latency_behavior": latency_analysis.get("tail_severity", "normal"),
        "latency_stability": latency_analysis.get("stability", "stable"),
        "error_concentration": error_analysis.get("error_concentration", "none"),
        "burst_detected": error_analysis.get("burst_detected", False),
        "throughput_trend": throughput_analysis.get("trend", "stable"),
        "degradation_detected": degradation.get("detected", False),
    }

    root_causes = []
    for pattern in KNOWN_FAILURE_PATTERNS:
        score = _match_pattern(observed, pattern["indicators"])
        if score > 0.3:
            confidence = "high" if score > 0.7 else "medium" if score > 0.5 else "low"
            evidence = _gather_evidence(observed, pattern["indicators"])
            root_causes.append({
                "pattern": pattern["pattern"],
                "description": pattern["description"],
                "confidence": confidence,
                "match_score": round(score, 2),
                "evidence": evidence,
            })

    root_causes.sort(key=lambda x: x["match_score"], reverse=True)

    return json.dumps({
        "root_causes": root_causes[:3],
        "observed_signals": observed,
    })


def _match_pattern(observed: dict, indicators: dict) -> float:
    matches = 0
    total = len(indicators)

    if "error_codes" in indicators:
        obs_codes = [c.lower() for c in observed["error_codes"]]
        if any(code.lower() in obs_codes for code in indicators["error_codes"]):
            matches += 1

    if "latency_behavior" in indicators:
        if observed["latency_behavior"] == indicators["latency_behavior"]:
            matches += 1
        elif observed["latency_stability"] == indicators["latency_behavior"]:
            matches += 0.5

    if "error_concentration" in indicators:
        if observed["error_concentration"] == indicators["error_concentration"]:
            matches += 1

    if "throughput_trend" in indicators:
        if observed["throughput_trend"] == indicators["throughput_trend"]:
            matches += 1

    if "degradation_detected" in indicators:
        if observed["degradation_detected"] == indicators["degradation_detected"]:
            matches += 1

    if "burst_detected" in indicators:
        if observed["burst_detected"] == indicators["burst_detected"]:
            matches += 1

    return matches / total if total > 0 else 0


def _gather_evidence(observed: dict, indicators: dict) -> list:
    evidence = []

    if "error_codes" in indicators:
        matching = [c for c in observed["error_codes"] if c.lower() in [i.lower() for i in indicators["error_codes"]]]
        if matching:
            evidence.append(f"Observed error codes: {', '.join(matching)}")

    if observed["degradation_detected"]:
        evidence.append("Performance degradation point detected during test")

    if observed["burst_detected"]:
        evidence.append("Error burst pattern detected (clustered failures)")

    if observed["throughput_trend"] == "declining":
        evidence.append("Throughput declined over the test duration")

    if observed["latency_behavior"] in ("heavy_tail", "extreme_tail"):
        evidence.append(f"Latency distribution shows {observed['latency_behavior'].replace('_', ' ')}")

    return evidence
