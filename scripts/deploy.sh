#!/bin/bash
set -euo pipefail

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║       PerfSage — Unified AWS Deployment         ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${PROJECT_DIR}/.env"
CDK_DIR="${PROJECT_DIR}/infra"

# ─── Check Prerequisites ────────────────────────────────────────────────────────
echo "▸ Checking prerequisites..."
MISSING=""
command -v python3 >/dev/null 2>&1 || MISSING="${MISSING}  - python3\n"
command -v pip3 >/dev/null 2>&1 || MISSING="${MISSING}  - pip3 (python package manager)\n"
command -v docker >/dev/null 2>&1 || MISSING="${MISSING}  - docker\n"
command -v aws >/dev/null 2>&1 || MISSING="${MISSING}  - aws CLI\n"
command -v cdk >/dev/null 2>&1 || MISSING="${MISSING}  - aws-cdk CLI (npm install -g aws-cdk)\n"

if [ -n "$MISSING" ]; then
    echo "ERROR: Missing required tools:"
    echo -e "$MISSING"
    exit 1
fi

docker info >/dev/null 2>&1 || { echo "ERROR: Docker is not running. Start Docker Desktop or run: colima start"; exit 1; }

echo "  ✓ All prerequisites met"
echo ""

# ─── Verify AWS Credentials ─────────────────────────────────────────────────────
echo "▸ Verifying AWS credentials..."
if ! aws sts get-caller-identity >/dev/null 2>&1; then
    echo "ERROR: AWS credentials not configured. Run: aws configure"
    exit 1
fi

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}

echo "  Account:  ${AWS_ACCOUNT_ID}"
echo "  Region:   ${AWS_REGION}"
echo "  ✓ Credentials valid"
echo ""

# ─── Step 1: Build Lambda Packages ───────────────────────────────────────────────
echo "▸ Step 1/4: Building Lambda packages..."
bash "${SCRIPT_DIR}/build_packages.sh"

# ─── Step 2: CDK Bootstrap (if needed) ──────────────────────────────────────────
echo "▸ Step 2/4: CDK bootstrap..."
cd "${CDK_DIR}"

# Create/activate venv for CDK dependencies
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt -q 2>/dev/null

if aws cloudformation describe-stacks --stack-name CDKToolkit --region ${AWS_REGION} >/dev/null 2>&1; then
    echo "  ✓ Already bootstrapped"
else
    echo "  Bootstrapping CDK (first-time)..."
    cdk bootstrap aws://${AWS_ACCOUNT_ID}/${AWS_REGION} 2>&1 | tail -3
    echo "  ✓ Bootstrapped"
fi
echo ""

# ─── Step 3: Deploy All Stacks via CDK ──────────────────────────────────────────
echo "▸ Step 3/4: Deploying infrastructure via CloudFormation..."
echo "  Stacks: PerfSageStorage, PerfSageNetworking, PerfSageExecution, PerfSage-Agents"
echo ""

cdk deploy --all --require-approval never --outputs-file "${PROJECT_DIR}/.cdk-outputs.json" 2>&1 | grep -E "(✅|❌|Outputs:)" || true
echo ""

# Verify deployment succeeded
if [ ! -f "${PROJECT_DIR}/.cdk-outputs.json" ]; then
    echo "ERROR: CDK deploy failed. Check: aws cloudformation describe-stack-events --stack-name PerfSageExecution"
    exit 1
fi

# Verify stacks exist
DEPLOYED_COUNT=$(aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --query "StackSummaries[?starts_with(StackName,'PerfSage')].StackName" --output text 2>/dev/null | wc -w | tr -d ' ')

if [ "$DEPLOYED_COUNT" -lt 4 ]; then
    echo "WARNING: Only ${DEPLOYED_COUNT}/4 stacks deployed. Check CloudFormation console."
fi

echo "  ✓ All infrastructure deployed via CloudFormation"
echo ""

# ─── Step 4: Build & Push Docker Image to ECR (created by CDK) ──────────────────
echo "▸ Step 4/4: Building and pushing k6 Docker image..."

ECR_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/perfsage/k6-runner"

aws ecr get-login-password --region ${AWS_REGION} | \
    docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com >/dev/null 2>&1

docker build --platform linux/amd64 -t perfsage-k6-runner -f "${PROJECT_DIR}/docker/Dockerfile.k6" "${PROJECT_DIR}" --quiet
docker tag perfsage-k6-runner:latest ${ECR_REPO}:latest
docker push ${ECR_REPO}:latest --quiet 2>/dev/null || docker push ${ECR_REPO}:latest 2>&1 | tail -1

echo "  ✓ k6 image pushed to ${ECR_REPO}"

# Build & push TestGen Docker image
echo "  Building TestGen agent Docker image..."
TESTGEN_ECR_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/perfsage/testgen-runner"

docker build --platform linux/amd64 -t perfsage-testgen-runner -f "${PROJECT_DIR}/docker/Dockerfile.testgen" "${PROJECT_DIR}" --quiet
docker tag perfsage-testgen-runner:latest ${TESTGEN_ECR_REPO}:latest
docker push ${TESTGEN_ECR_REPO}:latest --quiet 2>/dev/null || docker push ${TESTGEN_ECR_REPO}:latest 2>&1 | tail -1

echo "  ✓ TestGen image pushed to ${TESTGEN_ECR_REPO}"
echo ""

# ─── Auto-Configure .env ────────────────────────────────────────────────────────
echo "▸ Generating .env configuration..."

OUTPUTS_FILE="${PROJECT_DIR}/.cdk-outputs.json"

WEBSOCKET_URL=$(python3 -c "
import json
with open('${OUTPUTS_FILE}') as f:
    outputs = json.load(f)
net = outputs.get('PerfSageNetworking', {})
print(net.get('WebSocketManagementUrl', net.get('WebSocketUrl', '')))
" 2>/dev/null || echo "")

VPC_SUBNETS=$(python3 -c "
import json
with open('${OUTPUTS_FILE}') as f:
    outputs = json.load(f)
exe = outputs.get('PerfSageExecution', {})
net = outputs.get('PerfSageNetworking', {})
print(exe.get('PrivateSubnetIds', net.get('PrivateSubnets', '')))
" 2>/dev/null || echo "")

SECURITY_GROUP=$(python3 -c "
import json
with open('${OUTPUTS_FILE}') as f:
    outputs = json.load(f)
exe = outputs.get('PerfSageExecution', {})
print(exe.get('SecurityGroupId', ''))
" 2>/dev/null || echo "")

S3_BUCKET=$(python3 -c "
import json
with open('${OUTPUTS_FILE}') as f:
    outputs = json.load(f)
st = outputs.get('PerfSageStorage', {})
print(st.get('ResultsBucketName', 'perfsage-results-${AWS_ACCOUNT_ID}-${AWS_REGION}'))
" 2>/dev/null || echo "perfsage-results-${AWS_ACCOUNT_ID}-${AWS_REGION}")

cat > "${ENV_FILE}" <<EOF
# ┌──────────────────────────────────────────────────────────────┐
# │  PerfSage — Auto-generated by deploy.sh                     │
# │  Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")               │
# │  All resources managed by CloudFormation (cdk destroy)      │
# └──────────────────────────────────────────────────────────────┘

# AWS
AWS_REGION=${AWS_REGION}
AWS_ACCOUNT_ID=${AWS_ACCOUNT_ID}

# Storage (CloudFormation: PerfSageStorage)
PERFSAGE_S3_BUCKET=${S3_BUCKET}
PERFSAGE_S3_PREFIX=runs
PERFSAGE_DYNAMODB_TABLE=perfsage-test-runs
PERFSAGE_DYNAMODB_CONNECTIONS_TABLE=perfsage-ws-connections

# ECS / Fargate (CloudFormation: PerfSageExecution)
PERFSAGE_ECS_CLUSTER=perfsage-executor
PERFSAGE_ECS_TASK_FAMILY=perfsage-k6-runner
PERFSAGE_ECR_REPOSITORY=perfsage/k6-runner
PERFSAGE_FARGATE_SUBNETS=${VPC_SUBNETS}
PERFSAGE_FARGATE_SECURITY_GROUPS=${SECURITY_GROUP}

# WebSocket (CloudFormation: PerfSageNetworking)
PERFSAGE_WEBSOCKET_API_URL=${WEBSOCKET_URL}
PERFSAGE_WEBSOCKET_STAGE=prod

# Agent
PERFSAGE_EXECUTION_MODE=fargate
PERFSAGE_LOG_LEVEL=INFO
PERFSAGE_ANOMALY_AUTO_STOP=true

# Bedrock Model
PERFSAGE_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
PERFSAGE_MODEL_REGION=${AWS_REGION}

# Docker (for local mode — switch PERFSAGE_EXECUTION_MODE=local to use)
PERFSAGE_DOCKER_NETWORK=perfsage-net
PERFSAGE_K6_IMAGE=grafana/k6:latest
EOF

echo "  ✓ Configuration saved to .env"
echo ""

# ─── Done ────────────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════╗"
echo "║          ✓ Deployment Complete!                 ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║                                                ║"
echo "║  All resources in CloudFormation:              ║"
echo "║    • PerfSageStorage                           ║"
echo "║    • PerfSageNetworking                        ║"
echo "║    • PerfSageExecution                         ║"
echo "║    • PerfSage-Agents-{env}                     ║"
echo "║                                                ║"
echo "║  To delete everything:                         ║"
echo "║    cd infra && cdk destroy --all               ║"
echo "║                                                ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "  Run your first test:"
echo ""
echo "    perfsage-executor run -s tests/fixtures/sample_k6_script.js --vus 10 --duration 1m"
echo ""
