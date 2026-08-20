# Testing the Executor Agent on AWS

---

## What You Need

- An AWS account with admin permissions
- Docker (Docker Desktop or Colima)
- Python 3.11+
- Node.js (for CDK CLI: `npm install -g aws-cdk`)

---

## Setup (One Time)

### 1. Clone and install

```bash
cd perfsage-executor
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure AWS credentials

```bash
aws configure
```

### 3. Deploy everything

```bash
perfsage-executor deploy
```

That's it. This single command:

- Checks all prerequisites (Python, Docker, AWS CLI, CDK)
- Validates your AWS credentials
- Creates the ECR repository and pushes the k6 Docker image
- Deploys all infrastructure (VPC, ECS cluster, S3, DynamoDB, WebSocket API)
- Auto-generates `.env` with all resource IDs — no manual configuration

If any check fails, it tells you exactly what to fix before proceeding.

---

## Run a Test

```bash
# Quick verification (10 VUs, 1 minute)
perfsage-executor run -s tests/fixtures/sample_k6_script.js --vus 10 --duration 1m

# Heavy load test (500 VUs, 13 minutes)
perfsage-executor run -c tests/fixtures/heavy_load_config.json -s tests/fixtures/heavy_load_test.js

# Custom test
perfsage-executor run -s your_test.js --vus 200 --duration 10m --p99-threshold 500
```

No need to pass `--mode fargate` — after deploy, it defaults to Fargate automatically.

---

## Check Results

```bash
# View test status
perfsage-executor status --test-id <test-id>

# List all runs
perfsage-executor list

# Download raw results
aws s3 cp s3://perfsage-results-<account>-<region>/runs/<test-id>/ ./results/ --recursive
```

---

## Run Locally (Free, No AWS)

For quick iteration without AWS costs:

```bash
# Start a mock target API (from repo root)
docker compose -f docker/docker-compose.yml up -d mock-api

# Run locally
perfsage-executor run -s tests/fixtures/sample_k6_script.js --mode local --vus 20 --duration 2m

# Results at /tmp/perfsage/<test-id>/
```

---

## All Commands

| Command                                | What it does                                       |
| -------------------------------------- | -------------------------------------------------- |
| `perfsage-executor deploy`             | Deploy infrastructure (checks prerequisites first) |
| `perfsage-executor run -s test.js`     | Run a load test                                    |
| `perfsage-executor status --test-id X` | Check a test's result                              |
| `perfsage-executor list`               | Show recent test runs                              |
| `perfsage-executor abort --test-id X`  | Stop a running test                                |
| `perfsage-executor config`             | Show active configuration                          |
| `perfsage-executor destroy`            | Tear down all AWS resources                        |
| `perfsage-executor init`               | Check prerequisites only (deploy does this too)    |

---

## What Output Looks Like

### Real-time (WebSocket, every 1s):

```json
{
    "test_id": "run-abc123",
    "metrics": {
        "rps": 1250,
        "latency_p99_ms": 340,
        "error_rate_pct": 0.3,
        "active_vus": 500
    }
}
```

### Final summary (DynamoDB + S3):

```json
{
    "test_id": "run-abc123",
    "status": "completed",
    "summary": {
        "total_requests": 52340,
        "error_rate_pct": 2.3,
        "p99_latency_ms": 1200.0,
        "avg_rps": 67.1,
        "peak_vus": 500,
        "thresholds_passed": true
    }
}
```

---

## Cost

| Per test run (13 min, 500 VUs) | ~$0.11                                          |
| ------------------------------ | ----------------------------------------------- |
| When idle                      | $0 (except NAT Gateway ~$32/mo if left running) |

---

## Cleanup

```bash
perfsage-executor destroy
```

---

## Troubleshooting

| Problem                              | Fix                                                     |
| ------------------------------------ | ------------------------------------------------------- |
| Deploy fails at "Docker not running" | `colima start` or open Docker Desktop                   |
| Deploy fails at "AWS credentials"    | `aws configure`                                         |
| Deploy fails at CDK stacks           | Check CloudFormation console for details                |
| Test fails with "subnet error"       | Re-run `perfsage-executor deploy`                       |
| Target unreachable from Fargate      | Ensure target API is publicly accessible or in same VPC |
