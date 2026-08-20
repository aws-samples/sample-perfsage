/**
 * PerfSage Frontend API Client
 * 
 * All calls go through a Next.js server-side proxy at /api/perfsage/...
 * The proxy invokes Lambda directly (no CORS issues).
 * 
 * Routes:
 *   POST /api/perfsage/jobs              → TestGen Lambda
 *   GET  /api/perfsage/jobs/{id}         → TestGen Lambda
 *   POST /api/perfsage/executor/run      → Executor Lambda
 *   GET  /api/perfsage/executor/status/{id} → Executor Lambda
 *   POST /api/perfsage/analysis/run      → Analysis Lambda
 */

import type {
  TestGenInput,
  TestGenOutput,
  ExecutorOutput,
  AnalysisResult,
  TestSummary,
  SloThreshold,
  SloOperator,
} from "./types";
import { DEFAULT_SLOS, SUPPORTED_SLO_METRICS } from "./types";

const API_BASE = "/api/perfsage";

/** Coerce a possibly-string/undefined value (e.g. DynamoDB Number) to a finite number. */
function num(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

async function apiFetch(method: string, path: string, body?: object): Promise<any> {
  const response = await fetch(`${API_BASE}/${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return data;
}

// ─── TestGen Agent (Step 1) ──────────────────────────────────────────────────

export async function generateLoadTest(
  spec: string,
  prompt: string,
  dependencies: TestGenInput["dependencies"],
  records: string,
  context: string
): Promise<TestGenOutput> {
  const recordsObj: Record<string, number> = {};
  records.split("\n").forEach((line) => {
    const parts = line.split(":");
    if (parts.length === 2) {
      const key = parts[0].trim();
      const value = parseInt(parts[1].trim(), 10);
      if (key && !isNaN(value)) recordsObj[key] = value;
    }
  });

  const data = await apiFetch("POST", "jobs", {
    spec,
    prompt,
    format: spec.trim().startsWith("{") ? "json" : "yaml",
    dependencies,
    records: recordsObj,
    context,
  });

  if (data.script) return data as TestGenOutput;
  if (data.job_id) return pollTestGenJob(data.job_id);
  throw new Error("Unexpected response from TestGen");
}

async function pollTestGenJob(jobId: string): Promise<TestGenOutput> {
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, 10000));
    const data = await apiFetch("GET", `jobs/${jobId}`);
    if (data.status === "COMPLETE" && data.result) return data.result as TestGenOutput;
    if (data.status === "FAILED") throw new Error(data.error || "TestGen job failed");
  }
  throw new Error("TestGen job timed out");
}

// ─── Executor Agent (Step 2) ─────────────────────────────────────────────────

export async function runExecutorTest(
  script: string,
  vus: number,
  duration: string,
  targetUrl: string,
  targetRps?: number
): Promise<ExecutorOutput> {
  // Build payload — omit duration if empty so the backend auto-calculates it
  const payload: Record<string, unknown> = { script, vus, targetUrl };
  if (duration) payload.duration = duration;
  if (targetRps) payload.targetRps = targetRps;

  const data = await apiFetch("POST", "executor/run", payload);
  return {
    testId: data.test_id || "",
    status: "provisioning",
    summary: undefined,
    metricsLocation: undefined,
  };
}

export async function getTestStatus(testId: string): Promise<ExecutorOutput> {
  const data = await apiFetch("GET", `executor/status/${testId}`);
  const status = data.status || "running";

  let summary: TestSummary | undefined;
  if (data.summary) {
    const s = data.summary;
    // DynamoDB returns Number attributes as strings through the proxy, so
    // coerce every numeric field. Without this, downstream .toFixed() calls
    // throw "x.toFixed is not a function".
    summary = {
      totalRequests: num(s.total_requests),
      errorRate: num(s.error_rate_pct) / 100, // stored as percent; normalize to 0..1 fraction
      avgLatency: num(s.avg_latency_ms),
      p50Latency: num(s.p50_latency_ms),
      p90Latency: num(s.p90_latency_ms),
      p95Latency: num(s.p95_latency_ms),
      p99Latency: num(s.p99_latency_ms),
      rps: num(s.avg_rps),
      duration: "",
      vus: num(s.peak_vus),
    };
  }

  return {
    testId: data.test_id || testId,
    status: status === "completed" ? "complete" : (status as ExecutorOutput["status"]),
    summary,
    metricsLocation: data.metrics_location,
  };
}

// ─── Analysis Agent (Step 3) ─────────────────────────────────────────────────

export async function getAnalysisResult(
  testId: string,
  targetUrl?: string,
  slos: SloThreshold[] = DEFAULT_SLOS
): Promise<AnalysisResult> {
  // Drop incomplete rows (e.g. a "Custom…" row with no metric path yet) and
  // fall back to defaults if nothing usable remains.
  const usable = slos.filter((s) => s.metric && s.metric.trim() !== "");
  const sloDefs = usable.length > 0 ? usable : DEFAULT_SLOS;
  try {
    const data = await apiFetch("POST", "analysis/run", {
      test_run_id: testId,
      // Passing the target URL lets the Analysis agent scope X-Ray traces and
      // CloudWatch metrics to the app under test (server-side evidence).
      ...(targetUrl ? { target_url: targetUrl } : {}),
      // User-configurable SLO thresholds (Step 2). The backend evaluate_slos
      // tool consumes these verbatim.
      slo_definitions: sloDefs,
    });
    if (data.analysis_report) {
      // The Analysis Lambda returns a structured `summary` (from k6 summary.json)
      // alongside the markdown report, and — when SLOs were provided — an
      // authoritative deterministic `slo_results` + `slo_verdict`.
      return buildAnalysisFromReport(
        data.analysis_report,
        data.summary,
        sloDefs,
        data.slo_results,
        data.slo_verdict
      );
    }
  } catch {
    // Fall through to fallback
  }

  const testStatus = await getTestStatus(testId);
  if (!testStatus.summary) throw new Error("No results available yet");
  return buildFallbackAnalysis(testStatus.summary, sloDefs);
}

const EMPTY_SUMMARY: TestSummary = {
  totalRequests: 0, errorRate: 0, avgLatency: 0, p50Latency: 0,
  p90Latency: 0, p95Latency: 0, p99Latency: 0, rps: 0, duration: "", vus: 0,
};

/** Coerce a raw summary object (camelCase, errorRate as 0..1 fraction) to TestSummary. */
function normalizeSummary(s: any): TestSummary {
  if (!s || typeof s !== "object") return { ...EMPTY_SUMMARY };
  return {
    totalRequests: num(s.totalRequests),
    errorRate: num(s.errorRate),
    avgLatency: num(s.avgLatency),
    p50Latency: num(s.p50Latency),
    p90Latency: num(s.p90Latency),
    p95Latency: num(s.p95Latency),
    p99Latency: num(s.p99Latency),
    rps: num(s.rps),
    duration: typeof s.duration === "string" ? s.duration : "",
    vus: num(s.vus),
  };
}

// ─── SLO evaluation (client-side, mirrors the backend evaluate_slos tool) ─────

const OP_CHECK: Record<SloOperator, (a: number, t: number) => boolean> = {
  lt: (a, t) => a < t,
  lte: (a, t) => a <= t,
  gt: (a, t) => a > t,
  gte: (a, t) => a >= t,
};

const OP_LABEL: Record<SloOperator, string> = {
  lt: "<",
  lte: "≤",
  gt: ">",
  gte: "≥",
};

/** Maps an SLO metric path to the matching TestSummary field + display unit. */
const SLO_METRIC_ACCESS: Record<
  string,
  { get: (s: TestSummary) => number; unit: "ms" | "fraction" | "rps" | "count" }
> = {
  "latency.p50_ms": { get: (s) => s.p50Latency, unit: "ms" },
  "latency.p90_ms": { get: (s) => s.p90Latency, unit: "ms" },
  "latency.p95_ms": { get: (s) => s.p95Latency, unit: "ms" },
  "latency.p99_ms": { get: (s) => s.p99Latency, unit: "ms" },
  "latency.mean_ms": { get: (s) => s.avgLatency, unit: "ms" },
  error_rate: { get: (s) => s.errorRate, unit: "fraction" },
  rps_mean: { get: (s) => s.rps, unit: "rps" },
  vus_max: { get: (s) => s.vus, unit: "count" },
};

function fmtByUnit(v: number, unit: "ms" | "fraction" | "rps" | "count"): string {
  switch (unit) {
    case "ms":
      return `${v.toFixed(0)}ms`;
    case "fraction":
      return `${(v * 100).toFixed(2)}%`;
    case "rps":
      return v.toFixed(1);
    default:
      return `${v}`;
  }
}

/** Build threshold rows by evaluating each configured SLO against the summary.
 *  Metrics the client can't resolve locally are shown as "n/a" and don't fail
 *  the run (the backend still evaluates them authoritatively). */
function buildThresholds(
  s: TestSummary,
  slos: SloThreshold[]
): AnalysisResult["thresholds"] {
  return slos.map((slo) => {
    const access = SLO_METRIC_ACCESS[slo.metric];
    const opLabel = OP_LABEL[slo.operator] ?? "<";
    if (!access) {
      // Custom / unmapped metric: the client can't compute it, but the agent
      // evaluates it in the report. Mark not-evaluated so it's excluded from
      // the client-side verdict rather than silently counted as a pass.
      return {
        metric: slo.name || slo.metric,
        threshold: `${opLabel} ${slo.threshold}`,
        actual: "in report",
        passed: true,
        evaluated: false,
      };
    }
    const check = OP_CHECK[slo.operator] ?? OP_CHECK.lt;
    const actual = access.get(s);
    return {
      metric: slo.name || slo.metric,
      threshold: `${opLabel} ${fmtByUnit(slo.threshold, access.unit)}`,
      actual: fmtByUnit(actual, access.unit),
      passed: check(actual, slo.threshold),
      evaluated: true,
    };
  });
}

/** Infer a display unit for any metric path (covers server-side xray. and
 *  cloudwatch. paths the client-side SLO_METRIC_ACCESS map doesn't include). */
function unitForMetric(path: string): "ms" | "fraction" | "rps" | "count" {
  const known = SUPPORTED_SLO_METRICS.find((m) => m.value === path);
  if (known) return known.unit;
  if (path.endsWith("_ms")) return "ms";
  if (path === "error_rate" || path.endsWith("_rate")) return "fraction";
  if (path.startsWith("rps")) return "rps";
  return "count";
}

/** Build the threshold table from the backend's authoritative slo_results. */
function buildThresholdsFromBackend(rows: any[]): AnalysisResult["thresholds"] {
  return rows.map((r) => {
    const unit = unitForMetric(String(r.metric ?? ""));
    const opLabel = OP_LABEL[(r.operator as SloOperator)] ?? "";
    const evaluated = r.evaluated !== false;
    const thresholdNum = num(r.threshold);
    const actualNum = r.actual_value;
    return {
      metric: r.name || r.metric || "SLO",
      threshold: `${opLabel} ${fmtByUnit(thresholdNum, unit)}`.trim(),
      actual:
        evaluated && actualNum !== null && actualNum !== undefined
          ? fmtByUnit(num(actualNum), unit)
          : "in report",
      passed: Boolean(r.passed),
      evaluated,
    };
  });
}

function buildAnalysisFromReport(
  report: string,
  rawSummary: unknown,
  slos: SloThreshold[],
  sloResults?: unknown,
  sloVerdict?: unknown
): AnalysisResult {
  const hasSummary = rawSummary && typeof rawSummary === "object";
  const summary = hasSummary ? normalizeSummary(rawSummary) : { ...EMPTY_SUMMARY };

  // Prefer the backend's deterministic SLO evaluation (authoritative — it can
  // resolve server-side xray.*/cloudwatch.* metrics the browser can't).
  if (Array.isArray(sloResults) && sloResults.length > 0) {
    const thresholds = buildThresholdsFromBackend(sloResults);
    const verdict =
      sloVerdict === "pass" || sloVerdict === "fail" || sloVerdict === "warning"
        ? sloVerdict
        : detectVerdict(report);
    return { summary, thresholds, verdict, anomalies: [], recommendations: [], report };
  }

  // Fallback: evaluate client-side from the summary (older Lambda without
  // slo_results, or no SLOs provided).
  const thresholds = hasSummary ? buildThresholds(summary, slos) : [];
  const locallyEvaluated = thresholds.some((t) => t.evaluated !== false);
  return {
    summary,
    thresholds,
    verdict: locallyEvaluated ? verdictFromThresholds(thresholds) : detectVerdict(report),
    anomalies: [],
    recommendations: [],
    report,
  };
}

/** Verdict from the locally-evaluated SLO rows (custom/unmapped rows are
 *  excluded — the agent evaluates those in the report). Guarantees the banner
 *  matches the ✓/✗ results shown in the table for metrics we can compute. */
function verdictFromThresholds(
  thresholds: AnalysisResult["thresholds"]
): "pass" | "fail" {
  const evaluated = thresholds.filter((t) => t.evaluated !== false);
  return evaluated.every((t) => t.passed) ? "pass" : "fail";
}

/** Fallback verdict inference from report prose — used only when no structured
 *  SLO summary is available. Keys off the agent's explicit "Overall Verdict"
 *  line rather than scanning the whole report (which mentions fail/warning
 *  throughout its recommendations). */
function detectVerdict(report: string): "pass" | "fail" | "warning" {
  const lower = report.toLowerCase();
  const classify = (
    text: string | null | undefined
  ): "pass" | "fail" | "warning" | null => {
    if (!text) return null;
    if (/conditional|\bwarning\b/.test(text)) return "warning";
    if (/\bfail(?:ed|s|ure)?\b/.test(text)) return "fail";
    if (/\bpass(?:ed|es)?\b/.test(text)) return "pass";
    return null;
  };
  // Authoritative markers the analysis agent emits, in priority order:
  // "Overall SLO Verdict: PASS", "OVERALL: ✅ PASS", "Final Verdict: FAIL".
  const overall =
    lower.match(/overall[^\n]*verdict[^\n]{0,40}/)?.[0] ??
    lower.match(/overall:[^\n]{0,40}/)?.[0] ??
    lower.match(/final verdict[^\n]{0,60}/)?.[0];
  return classify(overall) ?? "pass";
}

function buildFallbackAnalysis(s: TestSummary, slos: SloThreshold[]): AnalysisResult {
  const thresholds = buildThresholds(s, slos);
  // Verdict comes from the configured SLO rows so the banner matches the table.
  const verdict = thresholds.length > 0 ? verdictFromThresholds(thresholds) : "pass";
  return {
    summary: s,
    thresholds,
    verdict,
    anomalies: [],
    recommendations:
      verdict === "pass"
        ? [{ category: "Performance", title: "All Good", description: "System performed within all configured SLO thresholds.", priority: "low" as const }]
        : [{ category: "Performance", title: "Investigate SLO Breach", description: "One or more configured SLO thresholds were not met.", priority: "high" as const }],
  };
}
