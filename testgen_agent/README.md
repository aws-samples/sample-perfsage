# PerfSage — TestGen Agent

AI-powered load test generator that transforms OpenAPI specs + natural language into executable k6 scripts.

## What it does

Takes user inputs (API spec, natural language prompt, resource dependencies, records count, business context) and generates an executable k6 load test script with:
- Dependency-aware data seeding (creates parent records first, captures IDs, injects into children)
- Realistic meaningful data (not random strings)
- Edge cases (large payloads, timeouts, invalid auth, concurrent writes)
- Weighted traffic distribution
- Test configuration JSON + hierarchy JSON for the Executor agent

## Architecture

- **Compute:** AWS Lambda (Python 3.12, 3GB, 15-min timeout)
- **AI Model:** Amazon Bedrock Claude Sonnet 4.6 via Strands Agents SDK
- **API:** API Gateway REST (IAM auth, async job pattern)
- **Storage:** DynamoDB (job state), S3 (spec storage)
- **IaC:** AWS CDK

## Input Fields

| Field | Required | Description |
|-------|----------|-------------|
| `spec` | Yes | OpenAPI/Swagger YAML or JSON |
| `prompt` | Yes | Natural language test description |
| `dependencies` | Yes (can be `[]`) | Resource relationships: `[{"parent": "company", "child": "department", "via": "company_id"}]` |
| `records` | Yes (can be `{}`) | Records per resource: `{"company": 100, "department": 1000}` |
| `context` | Yes (can be `""`) | Business domain description for meaningful data generation |

## Output

```json
{
  "script": "import http from 'k6/http';\n...",
  "config": {
    "test_type": "stress",
    "executor": {"type": "ramping-vus", "stages": [...]},
    "endpoints": [...],
    "auth_type": "oauth2"
  },
  "hierarchy": {
    "order": ["company", "department", "employee"],
    "delete_order": ["employee", "department", "company"],
    "dependencies": [...],
    "records": {"company": 100, "department": 1000, "employee": 50000}
  }
}
```

## Strands Tools

| Tool | Purpose |
|------|---------|
| `parse_api_spec` | Parses OpenAPI/Swagger, extracts endpoints, auth, schemas |
| `generate_scenario` | Converts NL + spec into test configuration |
| `generate_k6_script` | Produces final k6 JavaScript |
| `validate_script` | Validates imports, structure, threshold consistency |

## Deploy

```bash
# Install CDK dependencies
cd infra && pip install -r requirements.txt

# Build Lambda package
pip install --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --target ../lambda_package -r ../requirements.txt
cp -r ../*.py ../tools ../prompts ../lambda_package/

# Deploy (requires AWS credentials)
export AWS_REGION=us-east-1
cdk deploy --all -c env=dev
```

## Run Demo UI

```bash
pip install streamlit boto3
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...
streamlit run demo_ui.py
```

## Test Locally (Lambda Console)

1. Generate test event: `python3 make_test_event.py tests/fixtures/complex/hr_system.yaml "Stress test 100 users"`
2. Paste into Lambda Console → Test tab
3. Extract script: `pbpaste | python3 extract_script.py`
4. Run: `k6 run ~/Downloads/loadtest.js`

## Supported Specs

- OpenAPI 3.0, 3.1
- Swagger 2.0
- Up to 289KB (with smart truncation)
- Auth: Bearer, OAuth2, Basic, API Key, none

## Test Results

- Tested against live APIs (dummyjson.com, jsonplaceholder, petstore.swagger.io)
- 45,937 records seeded with 0% failure rate (3-level hierarchy)
- 100% checks passing on matching APIs
- Edge cases verified: large payloads, timeouts, concurrent writes
