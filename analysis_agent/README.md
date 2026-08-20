# PerfSage Analysis Agent

The Analysis Agent is one of three agents in the PerfSage system. It receives raw load test results from the Executor Agent and produces actionable performance analysis reports — grounded, wherever possible, in **server-side evidence** (AWS X-Ray traces + CloudWatch metrics) rather than inference from client-side numbers alone.

## What it does

Given raw k6 metrics stored in S3 and test metadata in DynamoDB, the agent:

1. **Ingests results** — pulls raw latency arrays, RPS time series, error counts, and error codes from S3 + DynamoDB
2. **Analyzes metrics** — computes statistical properties (skewness, kurtosis, coefficient of variation, tail ratios), detects throughput trends, identifies error bursts, and finds the exact point where performance degraded
3. **Fetches server-side evidence (ground truth)** — when the target app is observable:
   - **X-Ray traces** — actual Lambda cold-start (Init) durations, per-segment latency (API Gateway → Lambda handler → DynamoDB → downstream), and fault/error/throttle counts
   - **CloudWatch metrics** — Lambda throttles/concurrency/duration, API Gateway integration latency + 4XX/5XX, DynamoDB throttled/consumed capacity
4. **Identifies root causes** — correlates observed patterns against 6 known failure modes:
   - Connection pool exhaustion
   - Memory pressure
   - Thread/worker starvation
   - Rate limiting
   - Cold start penalty
   - Downstream timeout
5. **Generates recommendations** — maps each root cause to specific, actionable fixes (e.g., "Enable Provisioned Concurrency = 10") with priority levels (critical/high/medium/low) and categories (infrastructure/configuration/code/architecture)
6. **Evaluates SLOs** — checks results against user-defined thresholds (e.g., p99 < 2000ms, error rate < 5%) and returns a pass/fail/warning verdict
7. **Compares against baseline** — diffs current run vs a previous run, computes per-metric deltas, and runs a Kolmogorov-Smirnov test to detect statistically significant distribution shifts

### Evidence hierarchy (important)

X-Ray traces and CloudWatch metrics are treated as **ground truth and OUTRANK pattern-matched guesses**. The agent uses:
- **X-Ray** to attribute the latency tail to a specific component (Lambda init/cold start vs handler vs DynamoDB vs a downstream call), and
- **CloudWatch** to confirm throttling/capacity/error root causes with hard numbers (non-zero `Throttles` / `ThrottledRequests` / `5XXError`).

When server-side evidence contradicts the statistical guesser, the evidence wins — e.g. the agent will *dismiss* an inferred "memory pressure" hypothesis when CloudWatch shows zero throttles and zero errors. It only falls back to inferred root causes when X-Ray/CloudWatch data is unavailable.

## Architecture

```
┌───────────────────────────────────────────────────────────────┐
│                       Analysis Agent                            │
│                                                                │
│  Strands Agent (Claude Sonnet 4.6 via Bedrock)                 │
│  System prompt orchestrates tool calls in sequence             │
│                                                                │
│  Tools (8):                                                    │
│  ┌──────────────┐  ┌────────────────┐                          │
│  │ingest_results│→ │analyze_metrics │                          │
│  └──────────────┘  └────────────────┘                          │
│          │                  │                                  │
│          ▼                  ▼                                  │
│  ┌──────────────────┐  ┌────────────────────────┐             │
│  │fetch_xray_traces │  │fetch_cloudwatch_metrics │  ← evidence │
│  └──────────────────┘  └────────────────────────┘             │
│          │                  │                                  │
│          ▼                  ▼                                  │
│  ┌────────────────┐  ┌────────────────────────┐               │
│  │identify_root_  │→ │generate_recommendations │               │
│  │cause           │  │                         │               │
│  └────────────────┘  └────────────────────────┘               │
│          │                  │                                  │
│          ▼                  ▼                                  │
│  ┌──────────────┐  ┌────────────────┐                          │
│  │evaluate_slos │  │compare_runs    │                          │
│  └──────────────┘  └────────────────┘                          │
└───────────────────────────────────────────────────────────────┘
      │            │              │              │
      ▼            ▼              ▼              ▼
   S3 (raw    DynamoDB       AWS X-Ray      AWS CloudWatch
   metrics)   (run summary,  (traces of     (metrics of the
              test window)   target app)    target app)
```

## File structure

```
perfsage-initiative/
├── .env                      ← Environment configuration (model ID, bucket, table)
├── .aws-creds.sh             ← AWS credentials (source before running)
└── analysis_agent/
    ├── README.md             ← you are here
    ├── requirements.txt      ← runtime Python dependencies (bundled into Lambda)
    ├── requirements-dev.txt  ← dev deps (boto3 + pytest) for local runs/tests
    ├── __init__.py           ← package entry point
    ├── agent.py              ← Agent factory (create_analysis_agent) + system prompt
    ├── lambda_handler.py     ← AWS Lambda entry point + resource derivation
    ├── local_runner.py       ← Local dev runner with mocked AWS data
    ├── models/
    │   ├── __init__.py
    │   └── metrics.py        ← Data classes (TestMetrics, SLODefinition, AnalysisReport, etc.)
    ├── tools/
    │   ├── __init__.py       ← Exports ALL_TOOLS list
    │   ├── ingest_results.py           ← Pulls metrics from S3 + DynamoDB
    │   ├── analyze_metrics.py           ← Statistical analysis + degradation detection
    │   ├── fetch_xray_traces.py         ← X-Ray: cold starts, per-segment latency, faults
    │   ├── fetch_cloudwatch_metrics.py  ← CloudWatch: throttles, concurrency, 5xx, capacity
    │   ├── identify_root_cause.py       ← Pattern matching against known failure modes
    │   ├── generate_recommendations.py  ← Actionable fix suggestions
    │   ├── evaluate_slos.py             ← Pass/fail against SLO thresholds
    │   └── compare_runs.py              ← Baseline diff + KS distribution test
    └── tests/
        ├── __init__.py
        └── test_tools.py     ← Unit tests for the tools
```

## Setup

### Prerequisites

- Python 3.10+
- AWS credentials with Bedrock access (for the LLM) + S3/DynamoDB/X-Ray/CloudWatch/API Gateway read (for production use)

### Install dependencies

```bash
cd perfsage-initiative
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r analysis_agent/requirements-dev.txt
```

> `requirements.txt` holds only the slim runtime deps that get bundled into the
> Lambda package (no `boto3`/`pytest` — the Lambda runtime already provides
> `boto3`, and bundling it blows past the 250 MB unzipped limit).
> `requirements-dev.txt` adds `boto3` + `pytest` for local runs/tests. Build the
> Lambda package from `requirements.txt`, not `requirements-dev.txt`.

### Configure environment

**1. AWS credentials** — edit `.aws-creds.sh` with your Isengard credentials and source it:

```bash
source .aws-creds.sh
```

**2. Application configuration** — edit `.env` in the project root:

```
PERFSAGE_MODEL_ID=us.anthropic.claude-sonnet-4-6
PERFSAGE_S3_BUCKET=<perfsage-results>
PERFSAGE_DYNAMODB_TABLE=<perfsage-runs>
```

The `.env` file is loaded automatically by the agent via `python-dotenv`.

Available models (inference profile IDs):

| Model | ID |
|---|---|
| Claude Sonnet 4.6 (default) | `us.anthropic.claude-sonnet-4-6` |
| Claude Haiku 4.5 (cheapest/fastest) | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Claude Sonnet 4.5 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| Claude Opus 4.8 (most capable) | `us.anthropic.claude-opus-4-8` |

## How to run

### Run tests (no AWS or LLM needed)

```bash
source .venv/bin/activate
PYTHONPATH=. python -m pytest analysis_agent/tests/test_tools.py -v
```

### Run locally (with sample data)

```bash
source .venv/bin/activate
source .aws-creds.sh
PYTHONPATH=. python -m analysis_agent.local_runner
```

This uses mocked S3/DynamoDB with sample load test data. Requires Bedrock access for the LLM to orchestrate tool calls. (The X-Ray/CloudWatch tools will report `available: false` locally unless run against a real traced app.)

### Deploy as Lambda

The agent runs as a Lambda function (`perfsage-analysis-dev`) invoked after the Executor Agent finishes a test run, or directly from the PerfSage UI's Step 3.

**Important:** Do NOT create a Lambda Function URL — use API Gateway or the Lambda console test feature (per team policy).

#### Lambda environment variables

| Variable | Example | Description |
|---|---|---|
| `PERFSAGE_S3_BUCKET` | `perfsage-results` | S3 bucket where raw metrics are stored |
| `PERFSAGE_DYNAMODB_TABLE` | `perfsage-test-runs` | DynamoDB table with test run summaries (also used to resolve the test time window for X-Ray/CloudWatch) |
| `PERFSAGE_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` | (Optional) Override the default Bedrock model |

#### Lambda event format

The handler accepts both a **direct invoke** (fields at the top level) and an **API Gateway / proxy** event (JSON string in `event["body"]`). Fields:

```json
{
  "test_run_id": "run-123",
  "target_url": "https://g629epyold.execute-api.us-east-1.amazonaws.com/v1",
  "slo_definitions": [
    {"name": "P99 Latency", "metric": "latency.p99_ms", "threshold": 2000, "operator": "lt"},
    {"name": "Error Rate", "metric": "error_rate", "threshold": 0.05, "operator": "lt"}
  ],
  "baseline_s3_key": "runs/run-122/metrics.json"
}
```

| Field | Required | Why |
|---|---|---|
| `test_run_id` | **Yes** | The unique per-run identifier. Used as the DynamoDB key to fetch the run summary + test time window, and to build the default S3 path (`runs/{test_run_id}/metrics.json`). |
| `target_url` | No (recommended) | The URL of the app under test. Drives X-Ray filtering **and** server-side CloudWatch attribution — the handler derives the API Gateway + Lambda names from it (see below). This is all the UI sends. |
| `s3_key` | No | Defaults to `runs/{test_run_id}/metrics.json`. |
| `api_gateway_name` / `lambda_function_name` / `dynamodb_table_name` | No | Explicit CloudWatch target hints. If omitted, they're derived from `target_url`. Explicit values win over derived ones. |
| `slo_definitions` | No | Pass/fail thresholds, evaluated verbatim by the `evaluate_slos` tool. Fully configurable per run — exploratory/baseline runs may omit them for analysis without a verdict. |
| `baseline_s3_key` | No | Comparison only applies when a previous run exists. |

#### Automatic resource derivation (seamless from the UI)

The UI only asks the user for the **target URL** (entered in Step 2). The handler turns that into the CloudWatch target resources with no extra input:

1. `_extract_host(target_url)` → the API Gateway id (e.g. `g629epyold`), used as the X-Ray `filter_host`.
2. `_derive_resources(target_url)` → calls `apigateway:GET` to:
   - resolve the **API Gateway REST API name** (the `ApiName` CloudWatch dimension), and
   - read an integration URI to extract the **backing Lambda function name** (`_lambda_name_from_uri`).
3. The derived names are passed to `fetch_cloudwatch_metrics`. Explicit event fields override derived values; any derivation failure is swallowed (analysis still runs on X-Ray + client-side data).

The handler logs a line for observability:

```
Derived CloudWatch resources: apigw=<api-name> lambda=<fn-name>
```

#### Lambda IAM permissions (all read-only)

The Lambda execution role needs:

```json
{ "Effect": "Allow", "Action": ["dynamodb:GetItem"],
  "Resource": "arn:aws:dynamodb:us-east-1:<account-id>:table/perfsage-test-runs" },

{ "Effect": "Allow", "Action": ["s3:GetObject"],
  "Resource": "arn:aws:s3:::perfsage-results/*" },

{ "Effect": "Allow", "Action": ["bedrock:InvokeModelWithResponseStream"],
  "Resource": "*" },

{ "Effect": "Allow",
  "Action": ["xray:GetTraceSummaries", "xray:BatchGetTraces"],
  "Resource": "*" },

{ "Effect": "Allow",
  "Action": ["cloudwatch:GetMetricData", "cloudwatch:ListMetrics"],
  "Resource": "*" },

{ "Effect": "Allow", "Action": ["apigateway:GET"],
  "Resource": "arn:aws:apigateway:us-east-1::/restapis*" }
```

> The X-Ray and CloudWatch `Get*`/`ListMetrics` APIs only support `Resource: "*"` — this is the least privilege AWS permits for them. API Gateway read is scoped to `restapis*`. These are all managed in CDK (`infra/stacks/agents_stack.py`) as `XRayRead`, `CloudWatchMetricsRead`, and `ApiGatewayRead`.

## How the tools work

### ingest_results
**Input:** S3 bucket/key for raw metrics, DynamoDB table/key for run summary.
Downloads raw k6 JSON from S3 (latency arrays, RPS/error time series, error codes), fetches the run summary from DynamoDB, computes percentiles (p50/p90/p99), and returns a consolidated metrics JSON.

### analyze_metrics
**Input:** Consolidated metrics JSON from `ingest_results`.
- **Latency distribution** — skewness, kurtosis, coefficient of variation, tail ratio (p99/p50); classifies stability + tail severity
- **Throughput** — rolling mean, trend (stable/increasing/declining), sustained capacity (10th-percentile RPS)
- **Errors** — total count, burst detection, early/late/distributed concentration
- **Degradation point** — where latency first exceeded 2× baseline, and the RPS at that point

### fetch_xray_traces *(server-side evidence)*
**Input:** `test_run_id`, optional `filter_host` (the API Gateway id — strongly recommended so PerfSage's own agent traces aren't included).
- Resolves the test time window from the run's `started_at`/`ended_at` in DynamoDB (padded ±60s); falls back to the last 2 hours.
- Pulls X-Ray trace summaries for the window (counts, HTTP status distribution, faults/errors/throttles, response-time percentiles, services).
- Batch-gets the slowest ~10 traces, walks the segment tree, and extracts **cold starts** (Lambda `Initialization` subsegments → `count`, `avg_init_ms`, `max_init_ms`) and **per-segment latency** (`avg_ms`/`max_ms` per component).
- Returns `available: false` with a reason if no traces match (tracing off, filter miss, or bad window) so the agent knows to fall back.

**Output:** JSON with `trace_count`, `faults/errors/throttles`, `response_time_ms`, `cold_starts`, `segment_latency_ms`, and `slowest_traces`.

### fetch_cloudwatch_metrics *(server-side evidence)*
**Input:** `test_run_id` + any of `api_gateway_name`, `lambda_function_name`, `dynamodb_table_name` (derived from `target_url` by the handler).
- Resolves the same DynamoDB-based test window.
- Builds a `GetMetricData` batch (60s period) for whichever targets are provided:
  - **API Gateway:** `Count`, `Latency`, `IntegrationLatency`, `4XXError`, `5XXError`
  - **Lambda:** `Invocations`, `Errors`, `Throttles`, `ConcurrentExecutions` (max), `Duration` (avg)
  - **DynamoDB:** `ThrottledRequests`, `ConsumedRead/WriteCapacityUnits`, `SystemErrors`
- Aggregates each metric (`sum`/`max`/`avg`) and surfaces **red flags** — non-zero `Throttles` / `5XXError` / `SystemErrors` — for easy consumption.
- Returns `available: false` if no target identifiers were provided/derivable.

**Output:** JSON with per-metric aggregates, a `red_flags` list, and an interpretation hint.

### identify_root_cause
Scores 6 known failure patterns by matched indicators, filters to >30% match, ranks by confidence (high >70% / medium >50% / low >30%), and gathers evidence. Returns the top 3 root causes. Per the evidence hierarchy, X-Ray/CloudWatch findings override low-confidence pattern matches.

### generate_recommendations
Maps each root cause to 2–3 specific fixes, adds general recommendations from metric thresholds, deduplicates, and sorts by priority (critical → low).

### evaluate_slos
Resolves each SLO's metric path (dot notation, e.g. `latency.p99_ms`), applies the operator (`lt`/`lte`/`gt`/`gte`) against the threshold, and computes an overall verdict.

**Signature:** `evaluate_slos(metrics_json, slo_definitions_json, xray_json="", cloudwatch_json="")`. The agent passes the `fetch_xray_traces` and `fetch_cloudwatch_metrics` outputs so SLOs can target server-side signals; their scalars are merged under `xray.*` / `cloudwatch.*`.

Supported metric paths:

*Client-side (from `ingest_results`):* `latency.{p50_ms,p90_ms,p95_ms,p99_ms,mean_ms,max_ms}`, `error_rate` (0–1), `rps_mean`, `rps_max`, `total_requests`, `successful_requests`, `failed_requests`, `duration_seconds`, `vus_max`.

*Server-side (from X-Ray, when `xray_json` provided):* `xray.cold_starts`, `xray.cold_start_rate` (0–1), `xray.avg_init_ms`, `xray.max_init_ms`, `xray.faults`, `xray.errors`, `xray.throttles`, `xray.p99_ms`.

*Server-side (from CloudWatch, when `cloudwatch_json` provided):* `cloudwatch.lambda_throttles`, `cloudwatch.lambda_errors`, `cloudwatch.lambda_concurrency_max`, `cloudwatch.lambda_duration_avg`, `cloudwatch.api_5xx`, `cloudwatch.api_4xx`, `cloudwatch.api_integration_latency_avg`, `cloudwatch.dynamodb_throttled`.

**SLO thresholds are fully configurable per run.** They come in via the event's `slo_definitions` (nothing is hardcoded in the tool):

```json
{"name": "Cold-start rate", "metric": "xray.cold_start_rate", "threshold": 0.02, "operator": "lt"}
```

The PerfSage UI surfaces these as an editable **SLO Thresholds** list in Step 2 (a curated metric dropdown plus a **Custom…** free-text option for any metric path, defaulting to p99 < 2000ms and error rate < 0.05). The frontend evaluates the client-side metrics locally to keep the threshold table and PASS/FAIL banner consistent with the agent's verdict; `xray.*`/`cloudwatch.*` (and any custom path the browser can't compute) are shown as "in report" and evaluated authoritatively by the agent. Direct/API-Gateway invocations can pass any `slo_definitions` array.

### compare_runs
Fetches baseline metrics from S3, computes change % for 7 key metrics, classifies improved/degraded/unchanged (±2%), and runs a Kolmogorov-Smirnov two-sample test on the latency distributions (p < 0.05 = significant shift).

## Framework

Built with [AWS Strands Agents SDK](https://github.com/strands-agents/sdk-python):
- Tools are plain Python functions decorated with `@tool`, collected in `tools/__init__.py` as `ALL_TOOLS`
- The LLM (Claude Sonnet 4.6 on Bedrock) orchestrates tool calls based on the system prompt in `agent.py`
- Tool results feed back into the agent's context automatically — the agent chains calls sequentially
- Model is configurable via `.env` without code changes (`read_timeout=300` on the Bedrock client for long analyses)

## Integration with other PerfSage agents

```
TestGen Agent → k6 script + config
     ↓
Executor Agent → raw metrics (S3) + run summary incl. started_at/ended_at (DynamoDB)
     ↓
Analysis Agent → report + recommendations + verdict
                 (grounded in X-Ray + CloudWatch for the target app)
```

The Executor Agent writes:
1. Raw metrics JSON to S3 (latency array, RPS/error time series, error codes)
2. Run summary to DynamoDB (`test_id`, total/failed requests, duration, `vus_max`, and `started_at`/`ended_at` — the last two define the window the X-Ray/CloudWatch tools query)

The PerfSage UI invokes this agent from Step 3, passing `test_run_id` + the Step-2 `target_url`; everything else (X-Ray filtering, CloudWatch target resolution) is derived server-side.

## Security notes

- Do NOT commit `.aws-creds.sh` or `.env` with real credentials to git
- All AWS access used by this agent is read-only / least-privilege; wildcard `Resource: "*"` is used only where the X-Ray and CloudWatch Get APIs mandate it
