/** Dependency relationship between API resources */
export interface Dependency {
  parent: string;
  child: string;
  via: string;
}

/** Input payload for the TestGen Lambda */
export interface TestGenInput {
  spec: string;
  prompt: string;
  dependencies: Dependency[];
  records: string;
  context: string;
}

/** Output from the TestGen Lambda */
export interface TestGenOutput {
  script: string;
  config: Record<string, unknown>;
  hierarchy: Record<string, unknown>;
}

/** Input payload for the Executor Agent */
export interface ExecutorInput {
  script: string;
  vus: number;
  duration: string;
  targetUrl: string;
}

/** Status of a running test */
export type TestStatus =
  | "provisioning"
  | "running"
  | "streaming"
  | "complete"
  | "failed";

/** Output from the Executor Agent */
export interface ExecutorOutput {
  testId: string;
  status: TestStatus;
  summary?: TestSummary;
  metricsLocation?: string;
}

/** Summary metrics from a completed test */
export interface TestSummary {
  totalRequests: number;
  errorRate: number;
  avgLatency: number;
  p50Latency: number;
  p90Latency: number;
  p95Latency: number;
  p99Latency: number;
  rps: number;
  duration: string;
  vus: number;
}

/** A single threshold evaluation */
export interface ThresholdResult {
  metric: string;
  threshold: string;
  actual: string;
  passed: boolean;
  /** False when the client couldn't resolve this metric locally (e.g. a custom
   *  metric path). Such rows are shown as "in report" and excluded from the
   *  client-side verdict — the analysis agent evaluates them authoritatively. */
  evaluated?: boolean;
}

/** Sentinel dropdown value that switches the metric field to free-text entry. */
export const CUSTOM_METRIC_VALUE = "__custom__";

/** Comparison operator for an SLO threshold (matches the backend evaluate_slos tool). */
export type SloOperator = "lt" | "lte" | "gt" | "gte";

/** A user-configurable SLO threshold, sent to the analysis agent as `slo_definitions`. */
export interface SloThreshold {
  name: string;
  metric: string;
  threshold: number;
  operator: SloOperator;
}

/** Metric paths the analysis agent's evaluate_slos tool understands, with display
 *  metadata for the UI. `unit` drives how thresholds/actuals are formatted. */
export const SUPPORTED_SLO_METRICS: {
  value: string;
  label: string;
  unit: "ms" | "fraction" | "rps" | "count";
}[] = [
  { value: "latency.p50_ms", label: "P50 Latency", unit: "ms" },
  { value: "latency.p90_ms", label: "P90 Latency", unit: "ms" },
  { value: "latency.p95_ms", label: "P95 Latency", unit: "ms" },
  { value: "latency.p99_ms", label: "P99 Latency", unit: "ms" },
  { value: "latency.mean_ms", label: "Mean Latency", unit: "ms" },
  { value: "error_rate", label: "Error Rate", unit: "fraction" },
  { value: "rps_mean", label: "Mean RPS", unit: "rps" },
  { value: "vus_max", label: "Max VUs", unit: "count" },
  // Server-side signals (evaluated by the agent from X-Ray / CloudWatch — shown
  // as "in report" in the client table since the browser can't compute them).
  { value: "xray.cold_start_rate", label: "Cold-start rate (X-Ray)", unit: "fraction" },
  { value: "xray.max_init_ms", label: "Max init duration (X-Ray)", unit: "ms" },
  { value: "cloudwatch.lambda_throttles", label: "Lambda throttles (CloudWatch)", unit: "count" },
  { value: "cloudwatch.api_5xx", label: "API 5XX errors (CloudWatch)", unit: "count" },
  { value: "cloudwatch.dynamodb_throttled", label: "DynamoDB throttles (CloudWatch)", unit: "count" },
];

/** Defaults matching what the UI previously hardcoded. */
export const DEFAULT_SLOS: SloThreshold[] = [
  { name: "P99 Latency", metric: "latency.p99_ms", threshold: 2000, operator: "lt" },
  { name: "Error Rate", metric: "error_rate", threshold: 0.05, operator: "lt" },
];

/** An anomaly detected during the test */
export interface Anomaly {
  type: string;
  description: string;
  severity: "low" | "medium" | "high" | "critical";
  timestamp?: string;
}

/** A recommendation from the analysis agent */
export interface Recommendation {
  category: string;
  title: string;
  description: string;
  priority: "low" | "medium" | "high";
}

/** Complete analysis result from the Analysis Agent */
export interface AnalysisResult {
  summary: TestSummary;
  thresholds: ThresholdResult[];
  verdict: "pass" | "fail" | "warning";
  anomalies: Anomaly[];
  recommendations: Recommendation[];
  /** Full markdown report from the analysis agent (rendered as-is in the UI). */
  report?: string;
  reportUrl?: string;
}
