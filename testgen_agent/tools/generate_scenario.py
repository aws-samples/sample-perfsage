import json
import re
from strands import tool


@tool
def generate_scenario(parsed_spec: str, user_request: str) -> str:
    """Generate a load test scenario configuration from parsed API spec and user's natural language request.

    Interprets the user's intent (test type, VUs, duration, target endpoints) and produces
    a structured test configuration that will drive k6 script generation.

    Args:
        parsed_spec: JSON string from parse_api_spec containing endpoints and auth info.
        user_request: Natural language description of what the user wants to test.

    Returns:
        JSON string containing the test scenario configuration.
    """
    spec = json.loads(parsed_spec)
    endpoints = spec.get("endpoints", [])

    test_type = _detect_test_type(user_request)
    target_endpoints = _select_endpoints(endpoints, user_request)
    executor_config = _build_executor_config(user_request, test_type)
    auth_config = _build_auth_config(spec)

    scenario = {
        "test_type": test_type,
        "base_url": spec.get("base_url", "http://localhost"),
        "executor": executor_config,
        "endpoints": target_endpoints,
        "auth": auth_config,
        "thresholds": _default_thresholds(test_type),
        "think_time": _get_think_time(executor_config.get("type", "")),
    }

    return json.dumps(scenario, indent=2, default=str)


def _detect_test_type(request: str) -> str:
    r = request.lower()
    if any(w in r for w in ["smoke", "quick check", "verify"]):
        return "smoke"
    if any(w in r for w in ["soak", "endurance", "long run", "memory leak"]):
        return "soak"
    if any(w in r for w in ["spike", "sudden", "burst", "flash"]):
        return "spike"
    if any(w in r for w in ["stress", "break", "limit", "maximum"]):
        return "stress"
    if any(w in r for w in ["rps", "requests per second", "arrival rate", "throughput"]):
        return "throughput"
    return "load"


def _select_endpoints(endpoints: list, request: str) -> list:
    r = request.lower()

    path_match = re.search(r'(/[a-z0-9/_\-{}]+)', r)
    if path_match:
        target_path = path_match.group(1)
        matched = [e for e in endpoints if target_path in e["path"].lower()]
        if matched:
            return matched

    keyword_matches = []
    keywords = ["login", "auth", "checkout", "cart", "order", "payment",
                "search", "user", "product", "item", "upload"]
    for kw in keywords:
        if kw in r:
            keyword_matches.extend(
                [e for e in endpoints if kw in e["path"].lower() or kw in e.get("summary", "").lower()]
            )

    if keyword_matches:
        return keyword_matches

    if "all" in r or "full" in r or "entire" in r:
        return endpoints[:20]

    if "flow" in r:
        return endpoints[:10]

    return endpoints[:5]


def _build_executor_config(request: str, test_type: str) -> dict:
    r = request.lower()

    vus = _extract_number(r, ["users", "vus", "virtual users", "concurrent"])
    duration = _extract_duration(r)
    rps = _extract_number(r, ["rps", "requests per second", "req/s", "req/sec"])

    if rps:
        return {
            "type": "constant-arrival-rate",
            "rate": rps,
            "time_unit": "1s",
            "duration": duration or "3m",
            "pre_allocated_vus": min(rps, 100),
            "max_vus": rps * 2,
        }

    if "ramp" in r:
        stages = _extract_stages(r, vus or 100)
        if stages:
            return {"type": "ramping-vus", "stages": stages}

    if test_type == "smoke":
        return {"type": "constant-vus", "vus": vus or 3, "duration": duration or "1m"}
    elif test_type == "soak":
        return {"type": "constant-vus", "vus": vus or 50, "duration": duration or "4h"}
    elif test_type == "spike":
        peak = vus or 1000
        return {
            "type": "ramping-vus",
            "stages": [
                {"duration": "1m", "target": int(peak * 0.1)},
                {"duration": "30s", "target": peak},
                {"duration": "1m", "target": peak},
                {"duration": "30s", "target": int(peak * 0.1)},
                {"duration": "1m", "target": 0},
            ],
        }
    elif test_type == "stress":
        peak = vus or 200
        return {
            "type": "ramping-vus",
            "stages": [
                {"duration": "2m", "target": int(peak * 0.5)},
                {"duration": "5m", "target": peak},
                {"duration": "5m", "target": peak},
                {"duration": "2m", "target": 0},
            ],
        }
    else:
        v = vus or 50
        return {
            "type": "ramping-vus",
            "stages": [
                {"duration": "2m", "target": v},
                {"duration": duration or "5m", "target": v},
                {"duration": "1m", "target": 0},
            ],
        }


def _extract_number(text: str, keywords: list) -> int | None:
    for kw in keywords:
        pattern = rf'(\d[\d,]*)\s*{re.escape(kw)}'
        match = re.search(pattern, text)
        if match:
            return int(match.group(1).replace(",", ""))
        pattern = rf'{re.escape(kw)}\s*[:=]?\s*(\d[\d,]*)'
        match = re.search(pattern, text)
        if match:
            return int(match.group(1).replace(",", ""))
    return None


def _extract_duration(text: str) -> str | None:
    pattern = r'(?:for|duration|over)\s+(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hours?)'
    match = re.search(pattern, text)
    if match:
        num = match.group(1)
        unit = match.group(2)[0]
        return f"{num}{unit}"

    pattern = r'(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hours?)'
    match = re.search(pattern, text)
    if match:
        num = match.group(1)
        unit = match.group(2)[0]
        return f"{num}{unit}"
    return None


def _extract_stages(text: str, default_peak: int) -> list | None:
    ramp_pattern = r'(?:ramp|from)\s+(\d+)\s*(?:to|→|->)\s*(\d+)\s*(?:over|in)\s+(\d+)\s*(s|m|min|minutes?|h)'
    matches = re.findall(ramp_pattern, text)
    if matches:
        stages = []
        for start, end, dur, unit in matches:
            stages.append({"duration": f"{dur}{unit[0]}", "target": int(end)})
        return stages

    if "hold" in text:
        hold_match = re.search(r'hold\s+(?:for\s+)?(\d+)\s*(s|m|min|h)', text)
        if hold_match:
            return [
                {"duration": "2m", "target": default_peak},
                {"duration": f"{hold_match.group(1)}{hold_match.group(2)[0]}", "target": default_peak},
                {"duration": "1m", "target": 0},
            ]

    return None


def _build_auth_config(spec: dict) -> dict:
    schemes = spec.get("security_schemes", {})
    global_sec = spec.get("global_security", [])

    if not schemes and not global_sec:
        return {"type": "none"}

    for scheme_name, scheme in schemes.items():
        if scheme.get("type") == "http" and scheme.get("scheme") == "bearer":
            return {"type": "bearer", "env_var": "AUTH_TOKEN"}
        if scheme.get("type") == "apiKey":
            return {
                "type": "api_key",
                "header_name": scheme.get("name", "X-API-Key"),
                "location": scheme.get("in", "header"),
                "env_var": "API_KEY",
            }
        if scheme.get("type") == "oauth2":
            return {
                "type": "oauth2",
                "token_url": scheme.get("token_url"),
                "env_var_client_id": "CLIENT_ID",
                "env_var_client_secret": "CLIENT_SECRET",
            }
        if scheme.get("type") == "http" and scheme.get("scheme") == "basic":
            return {
                "type": "basic",
                "env_var_username": "USERNAME",
                "env_var_password": "PASSWORD",
            }

    return {"type": "none"}


def _default_thresholds(test_type: str) -> dict:
    base = {
        "http_req_failed": ["rate<0.01"],
        "http_req_duration": ["p(95)<500"],
    }
    if test_type == "smoke":
        base["http_req_duration"] = ["p(95)<2000"]
    elif test_type in ("stress", "spike"):
        base["http_req_failed"] = ["rate<0.05"]
        base["http_req_duration"] = ["p(95)<1000"]
    elif test_type == "soak":
        base["http_req_duration"] = ["p(95)<500", "avg<200"]
    return base


def _get_think_time(executor_type: str) -> dict:
    if "arrival-rate" in executor_type:
        return {"enabled": False}
    return {"enabled": True, "min_seconds": 1, "max_seconds": 3}
