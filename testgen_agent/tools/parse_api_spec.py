import json
import yaml
from typing import Any
from strands import tool


@tool
def parse_api_spec(spec_content: str, spec_format: str = "yaml") -> str:
    """Parse an OpenAPI/Swagger specification and extract all endpoint details needed for load test generation.

    Args:
        spec_content: The raw content of the OpenAPI spec (YAML or JSON string).
        spec_format: Format of the spec - "yaml" or "json".

    Returns:
        JSON string containing extracted endpoints, auth schemes, and server info.
    """
    if spec_format == "json":
        spec = json.loads(spec_content)
    else:
        spec = yaml.safe_load(spec_content)

    spec = _resolve_refs(spec, spec)

    result = {
        "openapi_version": spec.get("openapi") or spec.get("swagger", "2.0"),
        "info": {
            "title": spec.get("info", {}).get("title", "Unknown API"),
            "version": spec.get("info", {}).get("version", "1.0.0"),
        },
        "base_url": _extract_base_url(spec),
        "security_schemes": _extract_security_schemes(spec),
        "global_security": spec.get("security", []),
        "endpoints": _extract_endpoints(spec),
    }

    return json.dumps(result, indent=2, default=str)


def _extract_base_url(spec: dict) -> str:
    if "servers" in spec:
        server = spec["servers"][0]
        url = server.get("url", "")
        variables = server.get("variables", {})
        for var_name, var_def in variables.items():
            default = var_def.get("default", "")
            url = url.replace(f"{{{var_name}}}", default)
        # If URL is relative (no scheme), keep the path but note it needs a host.
        if url and not url.startswith("http"):
            # Try to infer host from spec title/description
            title = spec.get("info", {}).get("title", "").lower()
            if "petstore" in title:
                return f"https://petstore3.swagger.io{url.rstrip('/')}"
            return f"https://api.example.com{url.rstrip('/')}"
        return url.rstrip("/") if url else "https://api.example.com"
    elif "host" in spec:
        scheme = (spec.get("schemes") or ["https"])[0]
        host = spec["host"]
        base_path = spec.get("basePath", "")
        return f"{scheme}://{host}{base_path}".rstrip("/")
    return "https://api.example.com"


def _extract_security_schemes(spec: dict) -> dict:
    schemes = {}

    if "components" in spec:
        raw = spec.get("components", {}).get("securitySchemes", {})
    else:
        raw = spec.get("securityDefinitions", {})

    for name, scheme in raw.items():
        scheme_type = scheme.get("type", "")

        if scheme_type == "http":
            schemes[name] = {
                "type": "http",
                "scheme": scheme.get("scheme", "bearer"),
                "bearer_format": scheme.get("bearerFormat"),
            }
        elif scheme_type == "apiKey":
            schemes[name] = {
                "type": "apiKey",
                "in": scheme.get("in", "header"),
                "name": scheme.get("name", "X-API-Key"),
            }
        elif scheme_type == "oauth2":
            flows = scheme.get("flows") or scheme.get("flow", {})
            token_url = None
            if isinstance(flows, dict):
                for flow_name, flow_data in flows.items():
                    if isinstance(flow_data, dict) and "tokenUrl" in flow_data:
                        token_url = flow_data["tokenUrl"]
                        break
            schemes[name] = {
                "type": "oauth2",
                "token_url": token_url,
            }
        elif scheme_type == "basic":
            schemes[name] = {"type": "http", "scheme": "basic"}

    return schemes


def _extract_endpoints(spec: dict) -> list:
    endpoints = []
    http_methods = {"get", "post", "put", "delete", "patch", "head", "options"}

    for path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue

        path_params = path_item.get("parameters", [])

        for method in http_methods:
            operation = path_item.get(method)
            if not operation or not isinstance(operation, dict):
                continue

            if operation.get("deprecated", False):
                continue

            all_params = {(p.get("name"), p.get("in")): p for p in path_params}
            for p in operation.get("parameters", []):
                all_params[(p.get("name"), p.get("in"))] = p
            params = list(all_params.values())

            endpoint = {
                "path": path,
                "method": method.upper(),
                "operation_id": operation.get("operationId"),
                "summary": operation.get("summary", ""),
                "tags": operation.get("tags", []),
                "parameters": {
                    "path": [p for p in params if p.get("in") == "path"],
                    "query": [p for p in params if p.get("in") == "query"],
                    "header": [p for p in params if p.get("in") == "header"],
                },
                "request_body": _extract_request_body(operation, spec),
                "security": operation.get("security"),
                "responses": list(operation.get("responses", {}).keys()),
            }
            endpoints.append(endpoint)

    return endpoints


def _extract_request_body(operation: dict, spec: dict) -> dict | None:
    rb = operation.get("requestBody")
    if not rb:
        params = operation.get("parameters", [])
        body_params = [p for p in params if p.get("in") == "body"]
        if body_params:
            schema = body_params[0].get("schema", {})
            return {"content_type": "application/json", "schema": schema, "required": True}
        return None

    content = rb.get("content", {})
    for content_type in ["application/json", "multipart/form-data", "application/x-www-form-urlencoded"]:
        if content_type in content:
            schema = content[content_type].get("schema", {})
            return {
                "content_type": content_type,
                "schema": schema,
                "required": rb.get("required", False),
            }

    if content:
        first_ct = next(iter(content))
        return {
            "content_type": first_ct,
            "schema": content[first_ct].get("schema", {}),
            "required": rb.get("required", False),
        }

    return None


def _resolve_refs(obj: Any, root: dict, depth: int = 0) -> Any:
    if depth > 20:
        return obj

    if isinstance(obj, dict):
        if "$ref" in obj:
            ref_path = obj["$ref"]
            if ref_path.startswith("#/"):
                parts = ref_path[2:].split("/")
                resolved = root
                for part in parts:
                    part = part.replace("~1", "/").replace("~0", "~")
                    if isinstance(resolved, dict):
                        resolved = resolved.get(part, {})
                    else:
                        return obj
                return _resolve_refs(resolved, root, depth + 1)
            return obj
        return {k: _resolve_refs(v, root, depth + 1) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_refs(item, root, depth + 1) for item in obj]
    return obj
