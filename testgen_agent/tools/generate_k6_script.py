import json
import random
from faker import Faker
from strands import tool

fake = Faker()


@tool
def generate_k6_script(scenario_config: str) -> str:
    """Generate a complete, executable k6 load test script from a scenario configuration.

    Takes the structured scenario config (from generate_scenario) and produces
    a valid k6 JavaScript file with proper imports, options, setup, and default function.

    Args:
        scenario_config: JSON string containing test scenario (endpoints, executor, auth, thresholds).

    Returns:
        Complete k6 JavaScript script as a string, ready to execute.
    """
    config = json.loads(scenario_config)

    parts = []
    parts.append(_generate_imports(config))
    parts.append(_generate_options(config))
    parts.append(_generate_setup(config))
    parts.append(_generate_default_function(config))

    return "\n".join(parts)


def _generate_imports(config: dict) -> str:
    imports = ["import http from 'k6/http';", "import { check, sleep, group } from 'k6';"]

    auth_type = config.get("auth", {}).get("type", "none")
    if auth_type == "basic":
        imports.append("import encoding from 'k6/encoding';")

    if config.get("think_time", {}).get("enabled", True):
        imports.append(
            "import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';"
        )

    return "\n".join(imports) + "\n"


def _generate_options(config: dict) -> str:
    executor = config.get("executor", {})
    thresholds = config.get("thresholds", {})
    exec_type = executor.get("type", "constant-vus")

    options = {"thresholds": thresholds}

    if exec_type == "constant-vus":
        options["vus"] = executor.get("vus", 10)
        options["duration"] = executor.get("duration", "30s")
    elif exec_type == "ramping-vus":
        options["stages"] = executor.get("stages", [{"duration": "1m", "target": 10}])
    elif exec_type == "constant-arrival-rate":
        options["scenarios"] = {
            "load_test": {
                "executor": "constant-arrival-rate",
                "rate": executor.get("rate", 100),
                "timeUnit": executor.get("time_unit", "1s"),
                "duration": executor.get("duration", "3m"),
                "preAllocatedVUs": executor.get("pre_allocated_vus", 50),
                "maxVUs": executor.get("max_vus", 200),
            }
        }
    elif exec_type == "ramping-arrival-rate":
        options["scenarios"] = {
            "load_test": {
                "executor": "ramping-arrival-rate",
                "startRate": executor.get("start_rate", 10),
                "timeUnit": executor.get("time_unit", "1s"),
                "preAllocatedVUs": executor.get("pre_allocated_vus", 50),
                "maxVUs": executor.get("max_vus", 200),
                "stages": executor.get("stages", []),
            }
        }

    options_json = json.dumps(options, indent=2)
    return f"export const options = {options_json};\n"


def _generate_setup(config: dict) -> str:
    auth = config.get("auth", {})
    auth_type = auth.get("type", "none")
    base_url = config.get("base_url", "http://localhost")

    if auth_type == "bearer":
        env_var = auth.get("env_var", "AUTH_TOKEN")
        return f"""
export function setup() {{
  const token = __ENV.{env_var};
  if (!token) {{
    console.warn('Warning: {env_var} not set. Requests will be unauthenticated.');
  }}
  return {{ token: token || '' }};
}}
"""
    elif auth_type == "oauth2":
        token_url = auth.get("token_url") or f"{base_url}/oauth/token"
        client_id_var = auth.get("env_var_client_id", "CLIENT_ID")
        client_secret_var = auth.get("env_var_client_secret", "CLIENT_SECRET")
        return f"""
export function setup() {{
  const tokenRes = http.post('{token_url}', {{
    grant_type: 'client_credentials',
    client_id: __ENV.{client_id_var},
    client_secret: __ENV.{client_secret_var},
  }});
  const token = tokenRes.json('access_token');
  if (!token) {{
    console.error('Failed to obtain OAuth2 token');
  }}
  return {{ token: token || '' }};
}}
"""
    elif auth_type == "basic":
        user_var = auth.get("env_var_username", "USERNAME")
        pass_var = auth.get("env_var_password", "PASSWORD")
        return f"""
export function setup() {{
  const credentials = encoding.b64encode(`${{__ENV.{user_var}}}:${{__ENV.{pass_var}}}`);
  return {{ credentials }};
}}
"""
    elif auth_type == "api_key":
        env_var = auth.get("env_var", "API_KEY")
        return f"""
export function setup() {{
  const apiKey = __ENV.{env_var};
  if (!apiKey) {{
    console.warn('Warning: {env_var} not set.');
  }}
  return {{ apiKey: apiKey || '' }};
}}
"""
    return ""


def _generate_default_function(config: dict) -> str:
    endpoints = config.get("endpoints", [])
    auth = config.get("auth", {})
    base_url = config.get("base_url", "http://localhost")
    think_time = config.get("think_time", {})

    auth_type = auth.get("type", "none")

    has_setup = auth_type != "none"
    param_str = "(data)" if has_setup else "()"

    lines = [f"export default function {param_str} {{"]
    lines.append(f"  const BASE_URL = __ENV.BASE_URL || '{base_url}';")

    if auth_type == "bearer" or auth_type == "oauth2":
        lines.append("  const params = { headers: { 'Authorization': `Bearer ${data.token}` } };")
    elif auth_type == "basic":
        lines.append("  const params = { headers: { 'Authorization': `Basic ${data.credentials}` } };")
    elif auth_type == "api_key":
        header_name = auth.get("header_name", "X-API-Key")
        lines.append(f"  const params = {{ headers: {{ '{header_name}': data.apiKey }} }};")
    else:
        lines.append("  const params = {};")

    lines.append("")

    for i, ep in enumerate(endpoints):
        method = ep.get("method", "GET").lower()
        path = ep.get("path", "/")
        summary = ep.get("summary", "") or ep.get("operation_id", f"endpoint_{i}")

        resolved_path = _resolve_path_params(path, ep.get("parameters", {}).get("path", []))
        query_string = _build_query_string(ep.get("parameters", {}).get("query", []))
        full_path = resolved_path + query_string

        group_name = summary[:50] if summary else f"{method.upper()} {path}"
        lines.append(f"  group('{_escape_js(group_name)}', function () {{")

        if method in ("post", "put", "patch") and ep.get("request_body"):
            body = _generate_request_body(ep["request_body"])
            body_json = json.dumps(body, indent=4)
            lines.append(f"    const payload = JSON.stringify({body_json});")

            if auth_type != "none":
                lines.append(
                    f"    const res = http.{method}("
                    f"`${{BASE_URL}}{full_path}`, payload, "
                    f"Object.assign({{}}, params, {{ headers: Object.assign({{}}, params.headers, "
                    f"{{ 'Content-Type': 'application/json' }}) }}));"
                )
            else:
                lines.append(
                    f"    const res = http.{method}("
                    f"`${{BASE_URL}}{full_path}`, payload, "
                    f"{{ headers: {{ 'Content-Type': 'application/json' }} }});"
                )
        elif method == "delete":
            lines.append(
                f"    const res = http.del(`${{BASE_URL}}{full_path}`, null, params);"
            )
        else:
            lines.append(f"    const res = http.{method}(`${{BASE_URL}}{full_path}`, params);")

        expected_status = 200 if method == "get" else (201 if method == "post" else 200)
        lines.append(
            f"    check(res, {{ 'status is {expected_status}': (r) => r.status === {expected_status} }});"
        )
        lines.append("  });")
        lines.append("")

    if think_time.get("enabled", True):
        min_s = think_time.get("min_seconds", 1)
        max_s = think_time.get("max_seconds", 3)
        lines.append(f"  sleep(randomIntBetween({min_s}, {max_s}));")

    lines.append("}")

    return "\n".join(lines) + "\n"


def _resolve_path_params(path: str, path_params: list) -> str:
    for param in path_params:
        name = param.get("name", "id")
        schema = param.get("schema", {})
        sample = _generate_sample_value(schema, name)
        path = path.replace(f"{{{name}}}", f"${{{sample}}}")
    return path


def _build_query_string(query_params: list) -> str:
    if not query_params:
        return ""
    required = [p for p in query_params if p.get("required", False)]
    if not required:
        return ""
    parts = []
    for p in required[:5]:
        name = p.get("name", "param")
        schema = p.get("schema", {"type": "string"})
        value = _generate_sample_value(schema, name)
        parts.append(f"{name}={value}")
    return "?" + "&".join(parts)


def _generate_request_body(body_spec: dict) -> dict:
    schema = body_spec.get("schema", {})
    return _generate_from_schema(schema)


def _generate_from_schema(schema: dict) -> any:
    if not schema:
        return {}

    if "enum" in schema:
        return schema["enum"][0]

    if "allOf" in schema:
        merged = {}
        for sub in schema["allOf"]:
            val = _generate_from_schema(sub)
            if isinstance(val, dict):
                merged.update(val)
        return merged

    if "anyOf" in schema or "oneOf" in schema:
        options = schema.get("anyOf") or schema.get("oneOf")
        if options:
            return _generate_from_schema(options[0])

    schema_type = schema.get("type", "object")

    if schema_type == "object" or "properties" in schema:
        result = {}
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        for field_name, field_schema in properties.items():
            if field_schema.get("readOnly"):
                continue
            result[field_name] = _generate_from_schema(field_schema)
        return result

    if schema_type == "array":
        items = schema.get("items", {})
        return [_generate_from_schema(items)]

    if schema_type == "string":
        return _generate_string_value(schema)

    if schema_type == "integer":
        return random.randint(
            schema.get("minimum", 1),
            schema.get("maximum", 100),
        )

    if schema_type == "number":
        return round(random.uniform(
            schema.get("minimum", 0.0),
            schema.get("maximum", 100.0),
        ), 2)

    if schema_type == "boolean":
        return True

    return {}


def _generate_string_value(schema: dict) -> str:
    fmt = schema.get("format", "")
    format_map = {
        "email": lambda: fake.email(),
        "date": lambda: fake.date(),
        "date-time": lambda: fake.iso8601(),
        "uri": lambda: fake.url(),
        "url": lambda: fake.url(),
        "uuid": lambda: str(fake.uuid4()),
        "hostname": lambda: fake.hostname(),
        "ipv4": lambda: fake.ipv4(),
        "password": lambda: fake.password(),
    }
    if fmt in format_map:
        return format_map[fmt]()
    return fake.pystr(min_chars=5, max_chars=20)


def _generate_sample_value(schema: dict, name: str) -> str:
    schema_type = schema.get("type", "string")
    if schema_type == "integer":
        return str(random.randint(1, 100))
    if "id" in name.lower():
        return str(random.randint(1, 9999))
    return f"test-{name}"


def _escape_js(s: str) -> str:
    return s.replace("'", "\\'").replace("\n", " ")
