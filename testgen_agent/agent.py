import os
import re
from strands import Agent
from strands.models import BedrockModel

from tools import parse_api_spec, generate_scenario, generate_k6_script, validate_script
from prompts.system_prompt import TESTGEN_SYSTEM_PROMPT


def create_agent(model_id: str = None, region: str = None, tools_override: list = None) -> Agent:
    model_id = model_id or os.environ.get(
        "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
    )
    region = region or os.environ.get("BEDROCK_REGION", "us-east-1")

    model = BedrockModel(
        model_id=model_id,
        region_name=region,
        temperature=0.1,
        max_tokens=16384,
    )

    tools = tools_override if tools_override is not None else [
        parse_api_spec, generate_scenario, generate_k6_script, validate_script
    ]

    agent = Agent(
        name="PerfSage-TestGen",
        description="Transforms API specs and natural language into executable k6 load test scripts",
        model=model,
        system_prompt=TESTGEN_SYSTEM_PROMPT,
        tools=tools,
    )

    return agent


def _truncate_spec(spec_content: str, max_chars: int = 25000) -> str:
    """Truncate large specs to fit within agent context window.

    For specs > max_chars, we parse and extract only the essential structure:
    paths, methods, parameters, schemas (without descriptions/examples).
    """
    if len(spec_content) <= max_chars:
        return spec_content

    import yaml
    import json

    try:
        if spec_content.strip().startswith("{"):
            spec = json.loads(spec_content)
        else:
            spec = yaml.safe_load(spec_content)
    except Exception:
        return spec_content[:max_chars]

    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ["get", "post", "put", "delete", "patch", "head", "options"]:
            op = path_item.get(method)
            if not op or not isinstance(op, dict):
                continue
            op.pop("description", None)
            op.pop("x-codegen-request-body-name", None)
            responses = op.get("responses", {})
            for code, resp in list(responses.items()):
                if isinstance(resp, dict):
                    resp.pop("description", None)
                    content = resp.get("content", {})
                    for ct, media in list(content.items()):
                        if isinstance(media, dict):
                            media.pop("examples", None)
                            media.pop("example", None)

    if "components" in spec:
        schemas = spec["components"].get("schemas", {})
        for name, schema in list(schemas.items()):
            if isinstance(schema, dict):
                schema.pop("description", None)
                schema.pop("example", None)
                for prop in schema.get("properties", {}).values():
                    if isinstance(prop, dict):
                        prop.pop("description", None)
                        prop.pop("example", None)

    if "definitions" in spec:
        for name, defn in list(spec["definitions"].items()):
            if isinstance(defn, dict):
                defn.pop("description", None)
                defn.pop("example", None)
                for prop in defn.get("properties", {}).values():
                    if isinstance(prop, dict):
                        prop.pop("description", None)
                        prop.pop("example", None)

    result = yaml.dump(spec, default_flow_style=False)

    if len(result) > max_chars:
        paths = spec.get("paths", {})
        path_keys = list(paths.keys())
        while len(yaml.dump(spec, default_flow_style=False)) > max_chars and len(path_keys) > 10:
            removed = path_keys.pop()
            del spec["paths"][removed]
        result = yaml.dump(spec, default_flow_style=False)

    return result


def _get_creation_order(dependencies: list) -> list:
    """Topological sort of resources based on dependencies. Parents come first."""
    if not dependencies:
        return []

    parents = set()
    children = set()
    edges = {}

    for dep in dependencies:
        parent = dep.get("parent", "")
        child = dep.get("child", "")
        parents.add(parent)
        children.add(child)
        edges.setdefault(child, []).append(parent)

    all_resources = parents | children
    order = []
    visited = set()

    def visit(resource):
        if resource in visited:
            return
        visited.add(resource)
        for parent in edges.get(resource, []):
            visit(parent)
        order.append(resource)

    for resource in all_resources:
        visit(resource)

    return order


def generate_load_test(
    spec_content: str,
    user_request: str,
    spec_format: str = "yaml",
    dependencies: list = None,
    records: dict = None,
    context: str = None,
) -> dict:
    """End-to-end generation: spec + NL + dependencies + records + context → validated k6 script.

    Optimized flow: pre-calls deterministic tools (parse, scenario, baseline script)
    directly in Python, then gives the LLM only the refinement + validation task.
    Reduces Bedrock calls from 5-8 down to 2-3.
    """
    dependencies = dependencies or []
    records = records or {}
    context = context or ""

    truncated_spec = _truncate_spec(spec_content)
    was_truncated = len(truncated_spec) < len(spec_content)

    # --- Pre-call deterministic tools directly (no LLM needed) ---
    import json as _json
    from tools.parse_api_spec import parse_api_spec as _parse_spec
    from tools.generate_scenario import generate_scenario as _gen_scenario
    from tools.generate_k6_script import generate_k6_script as _gen_k6

    parsed_spec_json = _parse_spec(spec_content=truncated_spec, spec_format=spec_format)
    scenario_json = _gen_scenario(parsed_spec=parsed_spec_json, user_request=user_request)
    baseline_script = _gen_k6(scenario_config=scenario_json)

    # Parse scenario for the config output
    try:
        scenario_dict = _json.loads(scenario_json)
    except (_json.JSONDecodeError, TypeError):
        scenario_dict = {}

    # Extract target URL from user context/prompt if available
    url_override = ""
    url_match = re.search(r'https?://[^\s\'"<>]+', (context or "") + " " + user_request)
    if url_match:
        url_override = url_match.group(0).rstrip('.,;')

    # --- Build the focused LLM prompt (only refinement + validation needed) ---
    agent = create_agent(tools_override=[validate_script])

    truncation_note = ""
    if was_truncated:
        truncation_note = f"\nNOTE: The spec was large ({len(spec_content)} chars) and has been trimmed."

    dependency_section = ""
    if dependencies:
        dep_lines = [f"  - {dep['parent']} → {dep['child']} (child has field: {dep['via']})" for dep in dependencies]
        creation_order = _get_creation_order(dependencies)
        dependency_section = f"""
RESOURCE DEPENDENCIES (create in this order — parents first, children after):
{chr(10).join(dep_lines)}

Creation order: {' → '.join(creation_order)}
Delete order (reverse): {' → '.join(reversed(creation_order))}
"""
    else:
        dependency_section = """
RESOURCE DEPENDENCIES: NONE PROVIDED
Treat all resources as independent. Add a comment: // NOTE: No resource dependencies provided.
"""

    records_section = ""
    if records:
        total_records = sum(v for v in records.values() if isinstance(v, (int, float)))
        rec_lines = [f"  - {resource}: {count} records" for resource, count in records.items()]
        records_section = f"""
DATA SEEDING REQUIREMENTS (create this many records in setup()):
{chr(10).join(rec_lines)}
Total records: {total_records}
"""
        if total_records > 500:
            timeout_seconds = max(120, int(total_records / 40) + 60)
            records_section += f"\nIMPORTANT: Add setupTimeout: '{timeout_seconds}s' to the options object (default 60s is too short for {total_records} records). With http.batch() at 45 rps, seeding takes ~{total_records // 45} seconds plus overhead.\n"

    context_section = ""
    if context:
        context_section = f"""
BUSINESS CONTEXT (use this to generate meaningful, realistic data):
{context}
"""

    base_url_section = ""
    if url_override:
        base_url_section = f"""
TARGET BASE URL (use this in the script): {url_override}
The script MUST use: const BASE_URL = __ENV.BASE_URL || '{url_override}';
"""

    prompt = f"""Refine this baseline k6 load test script into a production-quality script.

USER REQUEST: {user_request}

BASELINE SCRIPT (generated from the API spec — improve it, don't start from scratch):
```javascript
{baseline_script}
```

PARSED API SPEC (endpoints, auth, schemas already extracted):
```json
{parsed_spec_json[:8000]}
```
{truncation_note}
{dependency_section}
{records_section}
{context_section}
{base_url_section}
INSTRUCTIONS:
1. Take the baseline script above and refine it into a production-quality k6 load test.
2. Add dependency-aware data seeding in setup() — create parents first, capture IDs, inject into children.
3. Add realistic data (meaningful names, addresses, not random strings).
4. Add edge cases (5-10%% of traffic): large payloads, concurrent writes, timeout probes.
5. Add weighted traffic distribution in default(): ~70%% reads, ~25%% writes, ~5%% mutations.
6. Use validate_script to check the final script for errors. Fix any errors found.
7. Output ONLY the complete validated k6 JavaScript file — from the first 'import' to the final closing brace.
   Do NOT output anything else — no explanations, no markdown fences, no commentary.
   If the user's request contains threshold expressions like 'p(95)<5000ms', implement them
   in the options.thresholds object — do not echo them back as raw text.
"""

    result = agent(prompt)
    script = _extract_k6_script(str(result))

    # Build config from pre-computed scenario
    config = _normalize_config(scenario_dict) if scenario_dict else {}

    # Build hierarchy for Executor agent
    hierarchy = {
        "order": _get_creation_order(dependencies) if dependencies else [],
        "dependencies": dependencies,
        "records": records,
        "delete_order": list(reversed(_get_creation_order(dependencies))) if dependencies else [],
    }

    return {
        "script": script,
        "config": config,
        "hierarchy": hierarchy,
        "model": os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        "spec_truncated": was_truncated,
        "disclaimer": None if dependencies else "No resource dependencies provided. Resources tested independently.",
    }


def _extract_config_from_messages(agent) -> dict:
    """Extract the test configuration JSON from the agent's generate_scenario tool result."""
    import json as _json

    try:
        for msg in agent.messages:
            if msg.get("role") != "user":
                continue
            for block in msg.get("content", []):
                if "toolResult" not in block:
                    continue
                for content_item in block["toolResult"].get("content", []):
                    text = content_item.get("text", "")
                    if not text:
                        continue
                    try:
                        candidate = _json.loads(text)
                        if "test_type" in candidate and "executor" in candidate:
                            return _normalize_config(candidate)
                    except (_json.JSONDecodeError, TypeError):
                        continue
    except (AttributeError, KeyError):
        pass

    return {}


def _normalize_config(scenario: dict) -> dict:
    """Transform the raw scenario dict into a clean config JSON for the API response."""
    executor = scenario.get("executor", {})
    auth = scenario.get("auth", {})
    think_time = scenario.get("think_time", {})
    endpoints = scenario.get("endpoints", [])

    return {
        "test_type": scenario.get("test_type", "load"),
        "executor": {
            "type": executor.get("type"),
            "vus": executor.get("vus"),
            "duration": executor.get("duration"),
            "rate": executor.get("rate"),
            "time_unit": executor.get("time_unit"),
            "stages": executor.get("stages"),
        },
        "endpoints": [
            {"method": e.get("method"), "path": e.get("path")}
            for e in endpoints[:20]
        ],
        "auth_type": auth.get("type", "none"),
        "thresholds": scenario.get("thresholds", {}),
        "think_time": {
            "enabled": think_time.get("enabled", True),
            "min_seconds": think_time.get("min_seconds"),
            "max_seconds": think_time.get("max_seconds"),
        },
        "base_url": scenario.get("base_url", ""),
    }


def _extract_k6_script(raw_output: str) -> str:
    """Extract pure k6 JavaScript from agent output, stripping any commentary."""
    # Remove markdown fences if present (support multiple language tags)
    if "```" in raw_output:
        match = re.search(r"```(?:javascript|js|typescript|k6|ecmascript)?\s*\n(.*?)```", raw_output, re.DOTALL)
        if match:
            extracted = match.group(1).strip()
            if _is_valid_k6_script(extracted):
                return extracted

    # Find the first line that starts with 'import ' or '//' followed by import
    lines = raw_output.split("\n")
    start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import "):
            start_idx = i
            break
        if stripped.startswith("//") and i < len(lines) - 1 and lines[i + 1].strip().startswith("import "):
            start_idx = i
            break

    # Find the last closing brace (end of the default function)
    end_idx = len(lines) - 1
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "}":
            end_idx = i
            break

    extracted = "\n".join(lines[start_idx:end_idx + 1])

    if _is_valid_k6_script(extracted):
        return extracted

    # Fallback: if extraction failed, try the full output
    if _is_valid_k6_script(raw_output):
        return raw_output

    raise ValueError(
        f"Failed to extract a valid k6 script from agent output. "
        f"Output was {len(raw_output)} chars but missing required k6 structure "
        f"(export default function, import statements). "
        f"This may be caused by k6-like syntax in the user prompt being echoed back."
    )


def _is_valid_k6_script(script: str) -> bool:
    """Check if extracted text is a valid k6 script (minimum structure)."""
    if not script or len(script.strip().splitlines()) < 10:
        return False
    if "export default function" not in script:
        return False
    if "import " not in script:
        return False
    return True
