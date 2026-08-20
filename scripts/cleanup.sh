#!/bin/bash
set -euo pipefail

echo "=== PerfSage — Cleanup ==="
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Clean up local Docker resources
echo "Cleaning up Docker resources..."
docker compose -f "${PROJECT_DIR}/docker/docker-compose.yml" down -v 2>/dev/null || true

# Remove perfsage containers
docker ps -a --filter "label=perfsage.component=k6-runner" -q | xargs -r docker rm -f 2>/dev/null || true

# Remove perfsage network
docker network rm perfsage-net 2>/dev/null || true

# Clean local results
echo "Cleaning local results..."
rm -rf /tmp/perfsage/

echo "✓ Local cleanup complete"
echo ""

# AWS cleanup (optional — prompted)
read -p "Destroy AWS infrastructure? (y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Destroying CDK stacks..."
    cd "${PROJECT_DIR}/infra"
    pip install -r requirements.txt -q
    cdk destroy --all --force
    echo "✓ AWS infrastructure destroyed"
fi

echo ""
echo "Cleanup complete!"
