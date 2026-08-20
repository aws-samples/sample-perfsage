#!/bin/bash
set -e

echo "=== PerfSage k6 Runner ==="
echo "Test ID: ${PERFSAGE_TEST_ID}"
echo "VUs: ${K6_VUS}"
echo "Duration: ${K6_DURATION}"
echo "========================="

# Check if script is provided via S3 or local mount
SCRIPT_PATH="/scripts/test.js"

if [ -n "${K6_SCRIPT_S3_URI}" ]; then
    echo "Downloading script from S3: ${K6_SCRIPT_S3_URI}"
    aws s3 cp "${K6_SCRIPT_S3_URI}" "${SCRIPT_PATH}"
fi

if [ ! -f "${SCRIPT_PATH}" ]; then
    echo "ERROR: No test script found at ${SCRIPT_PATH}"
    echo "Mount a script to /scripts/test.js or set K6_SCRIPT_S3_URI"
    exit 1
fi

# Inject setupTimeout into the script's options if K6_SETUP_TIMEOUT is set
# k6 does NOT support --setup-timeout as a CLI flag — it must be in the script's options object
if [ -n "${K6_SETUP_TIMEOUT}" ]; then
    echo "Injecting setupTimeout: ${K6_SETUP_TIMEOUT} into script options"
    # Check if setupTimeout is already defined
    if ! grep -q "setupTimeout" "${SCRIPT_PATH}"; then
        # Use python3 for reliable cross-platform text injection
        python3 -c "
import re, sys
with open('${SCRIPT_PATH}', 'r') as f:
    content = f.read()
# Match 'export const options = {' or 'export let options = {'
pattern = r'(export\s+(?:const|let)\s+options\s*=\s*\{)'
replacement = r\"\1\n  setupTimeout: '${K6_SETUP_TIMEOUT}',\"
new_content = re.sub(pattern, replacement, content, count=1)
if new_content == content:
    print('WARNING: Could not find options object to inject setupTimeout')
else:
    with open('${SCRIPT_PATH}', 'w') as f:
        f.write(new_content)
    print('setupTimeout injected successfully')
"
    else
        echo "setupTimeout already defined in script, skipping injection"
    fi
fi

echo "Running k6 test..."

# Handle graceful shutdown
trap 'echo "Received SIGTERM — shutting down gracefully"; kill -TERM $K6_PID 2>/dev/null; wait $K6_PID' SIGTERM

# Execute k6 with JSON + CSV output
# If K6_DURATION is empty or "0s", don't pass --duration (let the script control execution)
K6_DURATION_ARG=""
if [ -n "${K6_DURATION}" ] && [ "${K6_DURATION}" != "0s" ] && [ "${K6_DURATION}" != "" ]; then
    K6_DURATION_ARG="--duration ${K6_DURATION}"
fi

k6 run \
    --vus "${K6_VUS}" \
    ${K6_DURATION_ARG} \
    --out "json=/results/metrics.json" \
    --summary-export="/results/summary.json" \
    --log-output=stdout \
    "${SCRIPT_PATH}" &

K6_PID=$!

# Wait for k6 to complete.
# NOTE: k6 exits non-zero (code 99) when a threshold is crossed. Under `set -e`
# a failing `wait` would abort this script BEFORE the summary/S3-upload steps
# below, silently losing results for every failing test. Disable errexit for the
# entire post-k6 results phase so we always capture the exit code and persist
# results (summary + S3 upload) on a best-effort basis, regardless of pass/fail.
set +e
wait "$K6_PID"
EXIT_CODE=$?

echo "k6 exited with code: ${EXIT_CODE}"

# Export results as CSV if possible
if [ -f "/results/metrics.json" ]; then
    echo "Metrics saved to /results/metrics.json"
fi

if [ -f "/results/summary.json" ]; then
    echo "Summary saved to /results/summary.json"
    echo "--- Summary ---"
    cat /results/summary.json | jq '.' 2>/dev/null || cat /results/summary.json
else
    echo "WARNING: summary.json not generated (k6 may have been terminated)"
    # Generate a minimal summary from metrics.json if it exists
    if [ -f "/results/metrics.json" ]; then
        echo "Generating summary from metrics.json..."
        python3 -c "
import json, sys

metrics = []
with open('/results/metrics.json') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            metrics.append(json.loads(line))
        except:
            pass

# Extract http_req_duration points
durations = []
reqs = 0
errors = 0
vus_max = 0

for m in metrics:
    mtype = m.get('type', '')
    metric = m.get('metric', '')
    if mtype == 'Point':
        data = m.get('data', {})
        if metric == 'http_req_duration':
            durations.append(float(data.get('value', 0)))
        elif metric == 'http_reqs':
            reqs += 1
        elif metric == 'http_req_failed' and data.get('value', 0) > 0:
            errors += 1
        elif metric == 'vus' and float(data.get('value', 0)) > vus_max:
            vus_max = int(data.get('value', 0))

if not durations:
    print('No duration data found in metrics.json')
    sys.exit(0)

durations.sort()
n = len(durations)
p50 = durations[int(n * 0.50)] if n > 0 else 0
p90 = durations[int(n * 0.90)] if n > 0 else 0
p95 = durations[int(n * 0.95)] if n > 0 else 0
p99 = durations[int(n * 0.99)] if n > 0 else 0

summary = {
    'metrics': {
        'http_req_duration': {
            'values': {
                'avg': sum(durations) / n if n else 0,
                'min': min(durations) if durations else 0,
                'med': p50,
                'max': max(durations) if durations else 0,
                'p(90)': p90,
                'p(95)': p95,
                'p(99)': p99,
            }
        },
        'http_reqs': {
            'values': {
                'count': len(durations),
                'rate': len(durations) / max(1, (durations[-1] - durations[0]) if len(durations) > 1 else 30),
            }
        },
        'http_req_failed': {
            'values': {
                'rate': errors / max(1, len(durations)),
            }
        },
        'vus_max': {
            'values': {
                'max': vus_max or int('${K6_VUS}'),
            }
        },
    }
}

with open('/results/summary.json', 'w') as f:
    json.dump(summary, f)
print('Generated summary.json from metrics data')
" 2>/dev/null && echo "  ✓ summary.json generated" || echo "  Could not generate summary (python3 not available or parse error)"
    fi
fi

# Upload results to S3 if bucket is configured
if [ -n "${PERFSAGE_S3_BUCKET}" ]; then
    echo "Uploading results to S3..."
    aws s3 cp /results/ "s3://${PERFSAGE_S3_BUCKET}/${PERFSAGE_S3_PREFIX:-runs}/${PERFSAGE_TEST_ID}/" --recursive
    echo "Results uploaded to s3://${PERFSAGE_S3_BUCKET}/${PERFSAGE_S3_PREFIX:-runs}/${PERFSAGE_TEST_ID}/"
fi

exit ${EXIT_CODE}
