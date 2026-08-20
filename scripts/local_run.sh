#!/bin/bash
set -euo pipefail

echo "=== PerfSage Executor — Local Mode ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Check prerequisites
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is required"; exit 1; }
command -v docker compose >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 || { echo "ERROR: docker compose is required"; exit 1; }

SCRIPT_PATH="${1:-executor_agent/tests/fixtures/sample_k6_script.js}"
VUS="${2:-10}"
DURATION="${3:-30s}"

echo "Script: ${SCRIPT_PATH}"
echo "VUs: ${VUS}"
echo "Duration: ${DURATION}"
echo ""

# Start supporting services (mock API + LocalStack)
echo "Starting supporting services..."
docker compose -f "${PROJECT_DIR}/docker/docker-compose.yml" up -d mock-api localstack
echo "Waiting for services to be ready..."
sleep 5

# Run the executor agent locally
echo ""
echo "Running load test..."
perfsage-executor run \
    --script "${SCRIPT_PATH}" \
    --mode local \
    --vus ${VUS} \
    --duration ${DURATION}

echo ""
echo "=== Test Complete ==="
echo "Results stored in /tmp/perfsage/"
