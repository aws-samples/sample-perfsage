#!/bin/bash
set -euo pipefail

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║       PerfSage — Build Lambda Packages          ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

PLATFORM_ARGS="--platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all:"

# ── TestGen Lambda Package ───────────────────────────────────────────────────────
echo "▸ Building TestGen Lambda package..."
rm -rf testgen_agent/lambda_package
mkdir -p testgen_agent/lambda_package

pip3 install $PLATFORM_ARGS \
    --target testgen_agent/lambda_package \
    -r testgen_agent/requirements.txt \
    -q 2>/dev/null || pip3 install $PLATFORM_ARGS \
    --target testgen_agent/lambda_package \
    -r testgen_agent/requirements.txt

cp -r testgen_agent/*.py testgen_agent/lambda_package/
cp -r testgen_agent/tools testgen_agent/lambda_package/
cp -r testgen_agent/prompts testgen_agent/lambda_package/

echo "  ✓ testgen_agent/lambda_package built"

# ── Executor Lambda Package ──────────────────────────────────────────────────────
echo "▸ Building Executor Lambda package..."
rm -rf executor_lambda_package
mkdir -p executor_lambda_package

pip3 install $PLATFORM_ARGS \
    --target executor_lambda_package \
    -r executor_agent/requirements.txt \
    -q 2>/dev/null || pip3 install $PLATFORM_ARGS \
    --target executor_lambda_package \
    -r executor_agent/requirements.txt

cp -r executor_agent/src/perfsage_executor executor_lambda_package/

echo "  ✓ executor_lambda_package built"

# ── Analysis Lambda Package ──────────────────────────────────────────────────────
echo "▸ Building Analysis Lambda package..."
rm -rf analysis_lambda_package
mkdir -p analysis_lambda_package

pip3 install $PLATFORM_ARGS \
    --target analysis_lambda_package \
    -r analysis_agent/requirements.txt \
    -q 2>/dev/null || pip3 install $PLATFORM_ARGS \
    --target analysis_lambda_package \
    -r analysis_agent/requirements.txt

cp -r analysis_agent analysis_lambda_package/

# Trim test dirs + caches that don't run in Lambda
find analysis_lambda_package -type d -name tests -prune -exec rm -rf {} + 2>/dev/null || true
find analysis_lambda_package -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

echo "  ✓ analysis_lambda_package built"

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  ✓ All Lambda packages built successfully       ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
