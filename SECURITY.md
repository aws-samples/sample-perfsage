# Security

## Security Posture Checklist

| # | Requirement | Enforcement | Status |
|---|---|---|---|
| 1 | IAM least-privilege (TestGen agent) | Scoped to specific Bedrock actions, S3 bucket, DynamoDB table, ECS task def | ✅ |
| 2 | IAM least-privilege (Executor agent) | `bedrock:*`, `ecs:*` on `*` (overprivileged — see Hardening) | ⚠️ POC |
| 3 | No hardcoded secrets | Credentials via env vars + `.env.local` (gitignored) | ✅ |
| 4 | S3 public access blocked | `BlockPublicAccess.BLOCK_ALL` on all buckets | ✅ |
| 5 | S3 SSL enforced | `enforce_ssl=True` on spec bucket | ✅ |
| 6 | DynamoDB PITR | Enabled on `perfsage-test-runs` | ✅ |
| 7 | Non-root containers | `USER perfsage` in Dockerfile.testgen, Dockerfile.agent | ✅ |
| 8 | ECR image scanning | `image_scan_on_push=True` | ✅ |
| 9 | Private subnets | Fargate tasks in private subnets with NAT egress | ✅ |
| 10 | No inbound network access | Security group: zero inbound rules | ✅ |
| 11 | X-Ray tracing | Enabled on all Lambda functions | ✅ |
| 12 | API authentication | IAM auth on all API Gateway methods | ✅ |
| 13 | Container stop timeout | 120s graceful shutdown window | ✅ |
| 14 | Idempotency guards | Prevents duplicate Fargate tasks on Lambda retry | ✅ |

---

## Current Security Posture (POC)

This project is currently a **Proof of Concept** deployed in a non-production Isengard sandbox account. The following security controls are in place:

### Authentication & Authorization

| Layer | Current (POC) | Production Recommendation |
|---|---|---|
| API Gateway | IAM auth (`AuthorizationType.IAM`) on all agent endpoints | Add Cognito User Pool or OIDC for user-facing APIs |
| Sample/E-commerce APIs | **No auth** (public, intentional for load testing) | Add API keys or IAM auth for non-test environments |
| Frontend | Server-side proxy with AWS credentials in `.env.local` | Use Cognito with OIDC flow; never expose keys client-side |
| Lambda-to-Lambda | Self-invoke with IAM role | No change needed |
| Lambda-to-ECS | `iam:PassRole` scoped to specific role ARNs | No change needed |

### IAM Policies

| Component | Current Scope | Production Recommendation |
|---|---|---|
| TestGen Task Role | `bedrock:InvokeModel/Converse` on `foundation-model/anthropic.*` + inference profiles | No change — correctly scoped |
| TestGen Task Role | S3 read/write on spec bucket only | No change — correctly scoped |
| TestGen Task Role | DynamoDB on job table only | No change — correctly scoped |
| TestGen Task Role | Logs on `/perfsage/testgen:*` only | No change — correctly scoped |
| TestGen Lambda | `ecs:RunTask`, `ecs:DescribeTasks` on specific task def | No change — correctly scoped |
| TestGen Lambda | `iam:PassRole` on 2 specific role ARNs | No change — correctly scoped |
| Executor Lambda | `bedrock:*` on `*` | **FIX:** Scope to specific actions + model ARNs |
| Executor Lambda | `ecs:*` on `*` | **FIX:** Scope to `RunTask/DescribeTasks/StopTask` on cluster ARN |
| Executor Lambda | `logs:*` on `*` | **FIX:** Scope to specific log groups |
| ECS Task Role (k6) | S3 + DynamoDB + CloudWatch (scoped) | No change — correctly scoped |

### Data Protection

| Aspect | Current (POC) | Production Recommendation |
|---|---|---|
| S3 Encryption | Default S3 encryption (SSE-S3) | Upgrade to SSE-KMS with customer-managed key |
| S3 Public Access | `BLOCK_ALL` on all buckets | No change — correct |
| S3 SSL | `enforce_ssl=True` on spec bucket | Enforce on all buckets |
| DynamoDB Encryption | Default AWS-managed key | Upgrade to CMK for sensitive data |
| DynamoDB PITR | Enabled on `perfsage-test-runs` only | Enable on all tables |
| Secrets in code | None hardcoded; creds via env vars + `.env.local` (gitignored) | Use AWS Secrets Manager or Parameter Store |
| Transport | HTTPS enforced via API Gateway | Add TLS 1.2 minimum policy |

### Network Security

| Aspect | Current (POC) | Production Recommendation |
|---|---|---|
| VPC | Private subnets with NAT gateway | No change — correct |
| Security Groups | Egress-only (`allow_all_outbound=True`), zero inbound rules | Scope egress to HTTPS/443 only |
| Fargate Public IP | `DISABLED` on TestGen tasks | Ensure `DISABLED` on all tasks |
| API Gateway | Regional endpoint, public | Add WAF rules, rate limiting per client |

### Container Security

| Aspect | Current (POC) | Production Recommendation |
|---|---|---|
| Base Images | `python:3.12.4-slim`, `alpine:3.19` | Pin to SHA digest, not tag |
| Non-root User | ✅ `USER perfsage` in `Dockerfile.testgen` and `Dockerfile.agent` | Add to `Dockerfile.k6` |
| Read-only Root FS | Not configured | Enable `readonlyRootFilesystem: true` with tmpfs mounts |
| Image Scanning | `image_scan_on_push=True` on ECR repos | Add automated vulnerability alerts |
| Privileged Mode | Not used | Explicitly set `privileged: false` |

### Observability & Audit

| Aspect | Current (POC) | Production Recommendation |
|---|---|---|
| X-Ray Tracing | Enabled on all Lambdas | No change — correct |
| CloudWatch Logs | 1-week retention | Increase to 90+ days for compliance |
| Container Insights | Enabled on ECS cluster | No change — correct |
| API Gateway Logs | Access logging not configured | Enable access logging with request/response |
| CloudTrail | Account-level (default) | Ensure all API calls are logged |

---

## Production Hardening Checklist

For a production deployment, address the following:

### P0 — Must Fix Before Production

- [ ] Remove `bedrock:*`, `ecs:*`, `logs:*` wildcard policies from Executor Lambda
- [ ] Add authentication to frontend (Cognito or OIDC)
- [ ] Add `USER` directive to `Dockerfile.k6` (runs as root currently)
- [ ] Enable `readonlyRootFilesystem` on all Fargate task definitions
- [ ] Pin Docker base images to SHA digests
- [ ] Add WAF to API Gateway
- [ ] Scope Security Group egress to HTTPS/443 only

### P1 — Should Fix

- [ ] Upgrade S3 encryption to SSE-KMS with customer-managed key
- [ ] Enable DynamoDB PITR on all tables
- [ ] Increase CloudWatch log retention to 90+ days
- [ ] Add API Gateway access logging
- [ ] Set `TLS_1_2` as minimum on all HTTPS endpoints
- [ ] Add resource-based policies to Lambda functions
- [ ] Implement VPC endpoints for S3, DynamoDB, Bedrock (eliminate NAT gateway)

### P2 — Nice to Have

- [ ] Add cdk-nag AwsSolutions rule set enforcement
- [ ] Implement automated image vulnerability scanning with SNS alerts
- [ ] Add Config rules for drift detection
- [ ] Implement cross-account deployment pipeline
- [ ] Add GuardDuty integration

---

## Dependency Management

| Package | Purpose | Security Notes |
|---|---|---|
| `strands-agents` | AI agent framework | AWS-maintained, Bedrock integration |
| `boto3` | AWS SDK | AWS-maintained |
| `faker` | Test data generation | No network calls, used in container only |
| `prance` / `openapi-spec-validator` | API spec parsing | Input validation on user-provided specs |
| `aws-cdk-lib` | Infrastructure as Code | AWS-maintained |

---

## Threat Model (High-Level)

| Threat | Mitigation | Residual Risk |
|---|---|---|
| Malicious API spec injection | Spec is parsed with `yaml.safe_load`, no arbitrary code execution | Low — YAML bombs could cause OOM |
| Prompt injection via user request | System prompt has strict output format rules | Medium — LLM could generate malicious k6 code |
| Credential exposure in `.env.local` | Gitignored, temporary Isengard creds with 1-hour expiry | Low — creds expire quickly |
| Unauthorized Lambda invocation | IAM auth on all API Gateway methods | Low |
| Data exfiltration from Fargate tasks | Egress-only SG, no inbound; tasks run user-provided scripts | Medium — k6 script could send data to external URLs |
| DDoS via load test | API Gateway throttle limits (50-500 rps) + AWS account limits | Low — sandbox account |
| Docker socket exposure (local dev) | `localstack` and `executor-agent` in `docker/docker-compose.yml` reach Docker only through `docker-proxy` (`tecnativa/docker-socket-proxy`), which allow-lists container/image/network/volume endpoints and denies `EXEC`, `SWARM`, `SYSTEM`, `SECRETS`, `PLUGINS`; only the proxy container mounts the real `/var/run/docker.sock` (read-only) | Low — proxy still grants container creation, so a compromised `executor-agent` could launch sibling containers on the host. Never run this compose file on a shared or internet-reachable host. |

---

## Compliance Notes

This POC is deployed in a non-production account and is **not** subject to production compliance requirements. For production deployment:

- Ensure PCI DSS compliance if processing payment data
- Ensure SOC 2 controls for customer-facing deployments
- Follow AWS Well-Architected Security Pillar guidelines
- Implement least-privilege access across all components
