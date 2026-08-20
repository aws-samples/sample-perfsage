#!/bin/bash
set -e

echo "=== PerfSage TestGen Runner ==="
echo "Job ID: ${TESTGEN_JOB_ID}"
echo "========================="

INPUT_PATH="/tmp/testgen/input.json"

# Download input from S3
if [ -n "${TESTGEN_INPUT_S3_URI}" ]; then
    echo "Downloading input from S3: ${TESTGEN_INPUT_S3_URI}"
    aws s3 cp "${TESTGEN_INPUT_S3_URI}" "${INPUT_PATH}"
else
    echo "ERROR: TESTGEN_INPUT_S3_URI not set"
    exit 1
fi

if [ ! -f "${INPUT_PATH}" ]; then
    echo "ERROR: Input file not found at ${INPUT_PATH}"
    exit 1
fi

echo "Running TestGen agent..."

# Handle graceful shutdown
trap 'echo "Received SIGTERM — shutting down gracefully"; kill -TERM $RUNNER_PID 2>/dev/null; wait $RUNNER_PID' SIGTERM

# Run the Python agent
python3 /app/fargate_runner.py "${INPUT_PATH}" &
RUNNER_PID=$!

wait $RUNNER_PID
EXIT_CODE=$?

echo "TestGen runner exited with code: ${EXIT_CODE}"
exit ${EXIT_CODE}
