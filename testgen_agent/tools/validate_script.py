import re
import json
from strands import tool

VALID_K6_MODULES = {
    "k6", "k6/http", "k6/crypto", "k6/encoding", "k6/data",
    "k6/execution", "k6/html", "k6/metrics", "k6/timers",
    "k6/ws", "k6/websockets", "k6/net/grpc", "k6/browser",
    "k6/experimental/csv", "k6/experimental/fs", "k6/secrets",
    "k6/experimental/streams",
}

# What each k6 module actually exports — for verifying named imports
K6_MODULE_EXPORTS = {
    "k6": {"check", "sleep", "group", "fail", "randomSeed"},
    "k6/http": {"get", "post", "put", "del", "patch", "head", "options", "request",
                "asyncRequest", "batch", "file", "cookieJar", "setResponseCallback",
                "expectedStatuses", "CookieJar", "FileData", "Response"},
    "k6/data": {"SharedArray"},
    "k6/metrics": {"Counter", "Gauge", "Rate", "Trend"},
    "k6/encoding": {"b64encode", "b64decode"},
    "k6/crypto": {"createHash", "createHMAC", "hmac", "md4", "md5", "sha1",
                  "sha256", "sha384", "sha512", "sha512_224", "sha512_256",
                  "ripemd160", "randomBytes", "Hasher"},
    "k6/execution": {"vu", "scenario", "instance", "test"},
    "k6/html": {"parseHTML", "Element", "Selection"},
    "k6/timers": {"setTimeout", "clearTimeout", "setInterval", "clearInterval"},
}

# Functions that ONLY come from jslib — NOT from any k6 built-in
JSLIB_ONLY_EXPORTS = {"randomIntBetween", "randomItem", "randomString", "uuidv4",
                      "findBetween", "normalDistributionStages"}

NODE_PATTERNS = [
    (r"\brequire\s*\(", "Uses require() — k6 only supports ES module imports"),
    (r"\bprocess\.env\b", "Uses process.env — use __ENV instead"),
    (r"\bBuffer\b", "Uses Node.js Buffer — not available in k6"),
    (r"\b__dirname\b", "Uses __dirname — not available in k6"),
    (r"\b__filename\b", "Uses __filename — not available in k6"),
    (r"from\s+['\"]fs['\"]", "Imports Node.js 'fs' module — not available in k6"),
    (r"from\s+['\"]path['\"]", "Imports Node.js 'path' module — not available in k6"),
    (r"from\s+['\"]axios['\"]", "Imports axios — use k6/http instead"),
    (r"from\s+['\"]node-fetch['\"]", "Imports node-fetch — use k6/http instead"),
]


@tool
def validate_script(script: str) -> str:
    """Validate a generated k6 script for correctness and k6 compatibility.

    Performs multi-layer static validation:
    - Checks for required structural elements (exports, imports)
    - Detects Node.js-incompatible patterns
    - Validates import sources against k6 module allowlist
    - Checks for common k6 pitfalls

    Args:
        script: The k6 JavaScript script to validate.

    Returns:
        JSON string with validation result: {is_valid, errors, warnings, suggestions}
    """
    errors = []
    warnings = []
    suggestions = []

    _check_structure(script, errors)
    _check_imports(script, errors, warnings)
    _check_node_patterns(script, errors)
    _check_k6_patterns(script, errors, warnings)
    _check_common_mistakes(script, errors, suggestions)

    result = {
        "is_valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
    }

    return json.dumps(result, indent=2)


def _check_structure(script: str, errors: list):
    if "export default function" not in script:
        errors.append("Missing: export default function() {} — k6 requires this as the entry point")

    if "export const options" not in script and "export let options" not in script:
        errors.append("Missing: export const options — script should define test options")

    if "from 'k6/http'" not in script and 'from "k6/http"' not in script:
        if "http.get" in script or "http.post" in script or "http.put" in script or "http.del" in script:
            errors.append("Uses http methods but missing: import http from 'k6/http'")


def _check_imports(script: str, errors: list, warnings: list):
    # Parse all import statements and verify named exports exist in their modules
    named_import_pattern = r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]"
    for match in re.finditer(named_import_pattern, script):
        names = [n.strip() for n in match.group(1).split(",")]
        source = match.group(2)

        # Check if any jslib-only function is being imported from a k6 built-in
        for name in names:
            if name in JSLIB_ONLY_EXPORTS and not source.startswith("https://jslib.k6.io"):
                errors.append(
                    f"CRITICAL: '{name}' imported from '{source}' — WRONG MODULE. "
                    f"'{name}' only exists in 'https://jslib.k6.io/k6-utils/1.2.0/index.js'. "
                    f"k6/data only exports SharedArray."
                )

        # Check named imports against known module exports
        if source in K6_MODULE_EXPORTS:
            valid_exports = K6_MODULE_EXPORTS[source]
            for name in names:
                if name not in valid_exports:
                    errors.append(
                        f"'{name}' is not exported by '{source}'. "
                        f"Available exports: {', '.join(sorted(valid_exports))}"
                    )

    import_pattern = r"from\s+['\"]([^'\"]+)['\"]"
    imports = re.findall(import_pattern, script)

    for imp in imports:
        if imp.startswith("https://jslib.k6.io/"):
            continue
        if imp.startswith("./") or imp.startswith("../"):
            continue
        if imp not in VALID_K6_MODULES:
            errors.append(f"Invalid import: '{imp}' is not a k6 built-in module")


def _check_node_patterns(script: str, errors: list):
    for pattern, message in NODE_PATTERNS:
        if re.search(pattern, script):
            errors.append(message)


def _check_k6_patterns(script: str, errors: list, warnings: list):
    if re.search(r"async\s+function\s*\(", script) or re.search(r"export\s+default\s+async", script):
        if "k6/browser" not in script:
            errors.append("Uses async/await — k6 default function must be synchronous (except browser tests)")

    if re.search(r"\bhttp\.delete\s*\(", script):
        errors.append("Uses http.delete() — k6 uses http.del() (delete is a JS reserved word)")

    if re.search(r"\bcheck\s*\(", script):
        if "from 'k6'" not in script and 'from "k6"' not in script:
            if "check" not in script.split("from 'k6'")[0] if "from 'k6'" in script else True:
                warnings.append("Uses check() — ensure it's imported from 'k6'")

    if re.search(r"\bsleep\s*\(", script):
        if "from 'k6'" not in script and 'from "k6"' not in script:
            warnings.append("Uses sleep() — ensure it's imported from 'k6'")

    # Check for sleep() in arrival-rate scenarios
    if "arrival-rate" in script and "sleep(" in script:
        if "export default" in script:
            default_body = script.split("export default")[1]
            if "sleep(" in default_body:
                warnings.append(
                    "sleep() found in arrival-rate scenario — arrival-rate executors control pacing, "
                    "sleep() wastes VU time and reduces effective throughput"
                )

    if "SharedArray" in script:
        default_fn_match = re.search(r"export\s+default\s+function[\s\S]*?\{([\s\S]*)\}", script)
        if default_fn_match:
            fn_body = default_fn_match.group(1)
            if "new SharedArray" in fn_body:
                errors.append("SharedArray instantiated inside default function — must be in init context (top level)")


def _check_common_mistakes(script: str, errors: list, suggestions: list):
    if "thresholds" not in script:
        suggestions.append("Consider adding thresholds to options for pass/fail criteria")

    if "check(" not in script:
        suggestions.append("Consider adding check() assertions to validate responses")

    if re.search(r"http\.(get|post|put|del|patch)\s*\([^)]*localhost", script):
        if "__ENV.BASE_URL" not in script:
            suggestions.append("Hardcoded localhost URL detected — consider using __ENV.BASE_URL for flexibility")

    if "sleep" not in script and "arrival-rate" not in script:
        suggestions.append("No sleep() found — consider adding think time for realistic VU-based load patterns")

    _check_threshold_consistency(script, errors, suggestions)

    if not script.strip().startswith("import") and not script.strip().startswith("//"):
        errors.append("Script must start with 'import' or '//' — no commentary or explanation text before code")


def _check_threshold_consistency(script: str, errors: list, suggestions: list):
    has_req_failed_threshold = bool(re.search(r"http_req_failed.*?rate<0\.0[1-5]", script))

    sends_intentional_errors = bool(re.search(
        r"(status.*?[45]\d\d|/status/|error|invalid|unauthorized|forbidden)",
        script, re.IGNORECASE
    ))

    if has_req_failed_threshold and sends_intentional_errors:
        if "tag" not in script.lower() or not re.search(r"http_req_failed\{", script):
            errors.append(
                "Threshold conflict: http_req_failed is set strict (<1-5%) but the script "
                "intentionally sends requests expecting non-2xx responses. Either exclude "
                "those requests using tags, raise the threshold, or remove the error-testing logic."
            )
