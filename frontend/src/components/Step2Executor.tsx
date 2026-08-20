"use client";

import { useState } from "react";
import { CodeBlock } from "./CodeBlock";
import type {
  TestGenOutput,
  ExecutorOutput,
  SloThreshold,
  SloOperator,
} from "@/lib/types";
import { SUPPORTED_SLO_METRICS, CUSTOM_METRIC_VALUE } from "@/lib/types";

interface Step2ExecutorProps {
  testGenOutput: TestGenOutput;
  onExecute: (
    script: string,
    vus: number,
    targetUrl: string,
    targetRps: number
  ) => Promise<void>;
  isLoading: boolean;
  executorOutput: ExecutorOutput | null;
  error: string | null;
  onAnalyze: () => void;
  slos: SloThreshold[];
  onSlosChange: (slos: SloThreshold[]) => void;
}

const OPERATOR_OPTIONS: { value: SloOperator; label: string }[] = [
  { value: "lt", label: "< less than" },
  { value: "lte", label: "≤ at most" },
  { value: "gt", label: "> greater than" },
  { value: "gte", label: "≥ at least" },
];

const metricUnit = (metric: string) =>
  SUPPORTED_SLO_METRICS.find((m) => m.value === metric)?.unit ?? "";

const unitHint = (metric: string) => {
  switch (metricUnit(metric)) {
    case "ms":
      return "ms";
    case "fraction":
      return "0–1 (0.05 = 5%)";
    case "rps":
      return "req/s";
    case "count":
      return "count";
    default:
      return "";
  }
};

const statusLabels: Record<string, { label: string; color: string }> = {
  provisioning: { label: "Provisioning Infrastructure", color: "text-yellow-400" },
  running: { label: "Running Test", color: "text-blue-400" },
  streaming: { label: "Streaming Results", color: "text-purple-400" },
  complete: { label: "Complete", color: "text-green-400" },
  failed: { label: "Failed", color: "text-red-400" },
};

export function Step2Executor({
  testGenOutput,
  onExecute,
  isLoading,
  executorOutput,
  error,
  onAnalyze,
  slos,
  onSlosChange,
}: Step2ExecutorProps) {
  const [vus, setVus] = useState(10);
  const [targetUrl, setTargetUrl] = useState("");
  const [targetRps, setTargetRps] = useState(40);

  const handleRun = async (e: React.FormEvent) => {
    e.preventDefault();
    await onExecute(testGenOutput.script, vus, targetUrl, targetRps);
  };

  const updateSlo = (
    index: number,
    field: keyof SloThreshold,
    value: string | number
  ) => {
    onSlosChange(
      slos.map((s, i) => (i === index ? { ...s, [field]: value } : s))
    );
  };

  const addSlo = () => {
    onSlosChange([
      ...slos,
      { name: "P95 Latency", metric: "latency.p95_ms", threshold: 1000, operator: "lt" },
    ]);
  };

  const removeSlo = (index: number) => {
    onSlosChange(slos.filter((_, i) => i !== index));
  };

  const statusInfo = executorOutput
    ? statusLabels[executorOutput.status] || { label: executorOutput.status, color: "text-slate-400" }
    : null;

  const isComplete = executorOutput?.status === "complete";

  return (
    <div className="space-y-6">
      {/* Generated Script */}
      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
          </svg>
          Generated k6 Script
        </h2>
        <CodeBlock
          code={testGenOutput.script}
          language="javascript"
          title="load-test.js"
          maxHeight="400px"
        />
      </div>

      {/* Config & Hierarchy */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <CodeBlock
            code={JSON.stringify(testGenOutput.config, null, 2)}
            language="json"
            title="Configuration"
            collapsible
          />
        </div>
        <div className="card">
          <CodeBlock
            code={JSON.stringify(testGenOutput.hierarchy, null, 2)}
            language="json"
            title="Resource Hierarchy"
            collapsible
          />
        </div>
      </div>

      {/* Execution Config */}
      <form onSubmit={handleRun} className="card">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
          </svg>
          Execution Parameters
        </h2>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
          <div>
            <label htmlFor="vus" className="label">Virtual Users (VUs)</label>
            <input
              id="vus"
              type="number"
              value={vus}
              onChange={(e) => setVus(Number(e.target.value))}
              min={1}
              max={10000}
              className="input-field"
              required
            />
          </div>
          <div>
            <label htmlFor="targetRps" className="label">Target API RPS Capacity</label>
            <input
              id="targetRps"
              type="number"
              value={targetRps}
              onChange={(e) => setTargetRps(Number(e.target.value))}
              min={10}
              max={5000}
              className="input-field"
              required
            />
            <p className="text-xs text-slate-500 mt-1">Max RPS the target API can handle (controls seeding speed)</p>
          </div>
          <div>
            <label htmlFor="targetUrl" className="label">Target URL</label>
            <input
              id="targetUrl"
              type="url"
              value={targetUrl}
              onChange={(e) => setTargetUrl(e.target.value)}
              placeholder="https://api.example.com"
              className="input-field"
              required
            />
          </div>
        </div>

        <div className="flex items-center justify-between">
          <button
            type="submit"
            disabled={isLoading || !targetUrl}
            className="btn-primary flex items-center gap-2"
          >
            {isLoading ? (
              <>
                <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                Running...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                </svg>
                Run Performance Test
              </>
            )}
          </button>
        </div>
      </form>

      {/* Execution Status */}
      {executorOutput && (
        <div className="card">
          <h3 className="text-md font-semibold text-white mb-3">Execution Status</h3>
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <span className="text-sm text-slate-400">Test ID:</span>
              <code className="text-sm text-slate-200 bg-slate-700 px-2 py-0.5 rounded">
                {executorOutput.testId}
              </code>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-sm text-slate-400">Status:</span>
              <span className={`text-sm font-medium ${statusInfo?.color}`}>
                {statusInfo?.label}
              </span>
              {isLoading && executorOutput.status !== "complete" && (
                <svg className="animate-spin w-4 h-4 text-blue-400" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              )}
            </div>

            {/* Progress indicator */}
            <div className="flex gap-1 mt-2">
              {["provisioning", "running", "streaming", "complete"].map((step) => {
                const progressSteps = ["provisioning", "running", "streaming", "complete"];
                const currentIndex = progressSteps.indexOf(executorOutput.status);
                const stepIndex = progressSteps.indexOf(step);
                const isReached = stepIndex <= currentIndex;

                return (
                  <div
                    key={step}
                    className={`h-1.5 flex-1 rounded-full transition-colors ${
                      isReached ? "bg-blue-500" : "bg-slate-700"
                    }`}
                    aria-hidden="true"
                  />
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Results Summary — shown when test completes, before analysis */}
      {isComplete && (
        <div className="card border border-green-700/50">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <svg className="w-5 h-5 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Test Complete
          </h3>

          {executorOutput.summary && executorOutput.summary.totalRequests > 0 && (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-6">
                <div className="bg-slate-800 rounded-lg p-3 text-center">
                  <p className="text-xs text-slate-400 uppercase tracking-wide">Total Requests</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {executorOutput.summary.totalRequests.toLocaleString()}
                  </p>
                </div>
                <div className="bg-slate-800 rounded-lg p-3 text-center">
                  <p className="text-xs text-slate-400 uppercase tracking-wide">Avg RPS</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {executorOutput.summary.rps.toFixed(1)}
                  </p>
                </div>
                <div className="bg-slate-800 rounded-lg p-3 text-center">
                  <p className="text-xs text-slate-400 uppercase tracking-wide">p99 Latency</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {executorOutput.summary.p99Latency.toFixed(0)}ms
                  </p>
                </div>
                <div className="bg-slate-800 rounded-lg p-3 text-center">
                  <p className="text-xs text-slate-400 uppercase tracking-wide">Error Rate</p>
                  <p className={`text-xl font-bold mt-1 ${
                    executorOutput.summary.errorRate > 0.05 ? "text-red-400" : "text-green-400"
                  }`}>
                    {(executorOutput.summary.errorRate * 100).toFixed(2)}%
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-4 mb-6">
                <div className="bg-slate-800/50 rounded-lg p-2 text-center">
                  <p className="text-xs text-slate-500">p50</p>
                  <p className="text-sm font-medium text-slate-200">{executorOutput.summary.p50Latency.toFixed(0)}ms</p>
                </div>
                <div className="bg-slate-800/50 rounded-lg p-2 text-center">
                  <p className="text-xs text-slate-500">p90</p>
                  <p className="text-sm font-medium text-slate-200">{executorOutput.summary.p90Latency.toFixed(0)}ms</p>
                </div>
                <div className="bg-slate-800/50 rounded-lg p-2 text-center">
                  <p className="text-xs text-slate-500">p95</p>
                  <p className="text-sm font-medium text-slate-200">{executorOutput.summary.p95Latency.toFixed(0)}ms</p>
                </div>
              </div>
            </>
          )}

          {(!executorOutput.summary || executorOutput.summary.totalRequests === 0) && (
            <p className="text-sm text-slate-400 mb-4">
              Test completed successfully. Detailed metrics will be available in the analysis.
            </p>
          )}

          {/* SLO Thresholds — configurable, passed to the analysis agent */}
          <div className="border-t border-slate-700 pt-4 mb-4">
            <div className="flex items-center justify-between mb-1">
              <h4 className="text-sm font-medium text-slate-200">SLO Thresholds</h4>
              <button
                type="button"
                onClick={addSlo}
                className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-1 transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
                </svg>
                Add SLO
              </button>
            </div>
            <p className="text-xs text-slate-500 mb-3">
              Pass/fail criteria the analysis agent evaluates against. Defaults to
              p99 &lt; 2000ms and error rate &lt; 5%. Use <span className="text-slate-400">≤ at most</span> or{" "}
              <span className="text-slate-400">≥ at least</span> for &quot;or-equal&quot; comparisons
              (a strict = isn&apos;t a valid SLO operator).
            </p>

            <div className="space-y-3">
              {slos.map((slo, index) => {
                const isCustom = !SUPPORTED_SLO_METRICS.some((m) => m.value === slo.metric);
                return (
                  <div key={index} className="space-y-2">
                    <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
                      <input
                        type="text"
                        value={slo.name}
                        onChange={(e) => updateSlo(index, "name", e.target.value)}
                        placeholder="SLO name"
                        className="input-field flex-1 min-w-0"
                        aria-label={`SLO ${index + 1} name`}
                      />
                      <select
                        value={isCustom ? CUSTOM_METRIC_VALUE : slo.metric}
                        onChange={(e) =>
                          updateSlo(
                            index,
                            "metric",
                            e.target.value === CUSTOM_METRIC_VALUE ? "" : e.target.value
                          )
                        }
                        className="input-field sm:w-40"
                        aria-label={`SLO ${index + 1} metric`}
                      >
                        {SUPPORTED_SLO_METRICS.map((m) => (
                          <option key={m.value} value={m.value}>
                            {m.label}
                          </option>
                        ))}
                        <option value={CUSTOM_METRIC_VALUE}>Custom…</option>
                      </select>
                      <select
                        value={slo.operator}
                        onChange={(e) => updateSlo(index, "operator", e.target.value as SloOperator)}
                        className="input-field sm:w-40"
                        aria-label={`SLO ${index + 1} operator`}
                      >
                        {OPERATOR_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                      <div className="flex items-center gap-2 sm:w-56">
                        <input
                          type="number"
                          value={slo.threshold}
                          onChange={(e) => updateSlo(index, "threshold", Number(e.target.value))}
                          step="any"
                          min={0}
                          className="input-field flex-1 min-w-[6rem]"
                          aria-label={`SLO ${index + 1} threshold`}
                        />
                        <span className="text-xs text-slate-500 whitespace-nowrap">
                          {isCustom ? "value" : unitHint(slo.metric)}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => removeSlo(index)}
                        className="text-red-400 hover:text-red-300 p-2 rounded-lg hover:bg-slate-700 transition-colors self-center"
                        aria-label={`Remove SLO ${index + 1}`}
                        disabled={slos.length === 1}
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>

                    {isCustom && (
                      <div className="sm:pl-2">
                        <input
                          type="text"
                          value={slo.metric}
                          onChange={(e) => updateSlo(index, "metric", e.target.value)}
                          placeholder="Custom metric path, e.g. latency.max_ms or rps_max"
                          className="input-field w-full font-mono text-xs"
                          aria-label={`SLO ${index + 1} custom metric path`}
                        />
                        <p className="text-xs text-slate-500 mt-1">
                          Dot-path into the metrics the agent computes (e.g.{" "}
                          <code className="text-slate-400">latency.max_ms</code>,{" "}
                          <code className="text-slate-400">rps_max</code>,{" "}
                          <code className="text-slate-400">failed_requests</code>). Evaluated in the
                          detailed report; shown as &quot;in report&quot; in the table above.
                        </p>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="flex items-center justify-between border-t border-slate-700 pt-4">
            <p className="text-sm text-slate-400">
              Run the AI analysis agent for deeper insights and recommendations.
            </p>
            <button
              type="button"
              onClick={onAnalyze}
              className="btn-primary flex items-center gap-2"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
              Run Analysis →
            </button>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 flex items-start gap-3" role="alert">
          <svg className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div>
            <p className="text-sm font-medium text-red-300">Execution Failed</p>
            <p className="text-sm text-red-400 mt-1">{error}</p>
          </div>
        </div>
      )}
    </div>
  );
}
