"use client";

import type { AnalysisResult } from "@/lib/types";
import { MarkdownReport } from "./MarkdownReport";

interface Step3AnalysisProps {
  analysisResult: AnalysisResult | null;
  isLoading: boolean;
  error: string | null;
}

export function Step3Analysis({
  analysisResult,
  isLoading,
  error,
}: Step3AnalysisProps) {
  if (isLoading) {
    return (
      <div className="card flex flex-col items-center justify-center py-16">
        <svg className="animate-spin w-10 h-10 text-blue-400 mb-4" fill="none" viewBox="0 0 24 24" aria-hidden="true">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
        <p className="text-slate-300 text-lg">Analyzing test results...</p>
        <p className="text-slate-500 text-sm mt-1">This may take a moment</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-900/30 border border-red-700 rounded-lg p-6 flex items-start gap-3" role="alert">
        <svg className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <div>
          <p className="text-sm font-medium text-red-300">Analysis Failed</p>
          <p className="text-sm text-red-400 mt-1">{error}</p>
        </div>
      </div>
    );
  }

  if (!analysisResult) {
    return (
      <div className="card flex flex-col items-center justify-center py-16">
        <svg className="w-16 h-16 text-slate-600 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
        </svg>
        <p className="text-slate-400 text-lg">No analysis available yet</p>
        <p className="text-slate-500 text-sm mt-1">
          Complete a test execution to see the analysis
        </p>
      </div>
    );
  }

  const { summary, thresholds, verdict, anomalies, recommendations, report, reportUrl } = analysisResult;

  const verdictStyles = {
    pass: { bg: "bg-green-900/30", border: "border-green-700", text: "text-green-400", label: "PASS" },
    fail: { bg: "bg-red-900/30", border: "border-red-700", text: "text-red-400", label: "FAIL" },
    warning: { bg: "bg-yellow-900/30", border: "border-yellow-700", text: "text-yellow-400", label: "WARNING" },
  };

  const verdictStyle = verdictStyles[verdict];

  return (
    <div className="space-y-6">
      {/* Verdict Banner */}
      <div className={`${verdictStyle.bg} ${verdictStyle.border} border rounded-xl p-6 text-center`}>
        <p className={`text-3xl font-bold ${verdictStyle.text}`}>
          {verdictStyle.label}
        </p>
        <p className="text-slate-300 mt-2">Performance Test Verdict</p>
      </div>

      {/* Summary Metrics */}
      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          Test Summary
        </h2>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <MetricCard label="Total Requests" value={summary.totalRequests.toLocaleString()} />
          <MetricCard
            label="Error Rate"
            value={`${(summary.errorRate * 100).toFixed(2)}%`}
            highlight={summary.errorRate > 0.01 ? "red" : "green"}
          />
          <MetricCard label="Avg Latency" value={`${summary.avgLatency.toFixed(0)}ms`} />
          <MetricCard label="RPS" value={summary.rps.toFixed(1)} />
        </div>

        <div className="mt-4 pt-4 border-t border-slate-700">
          <h3 className="text-sm font-medium text-slate-300 mb-3">Latency Percentiles</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <MetricCard label="P50" value={`${summary.p50Latency.toFixed(0)}ms`} small />
            <MetricCard label="P90" value={`${summary.p90Latency.toFixed(0)}ms`} small />
            <MetricCard label="P95" value={`${summary.p95Latency.toFixed(0)}ms`} small />
            <MetricCard label="P99" value={`${summary.p99Latency.toFixed(0)}ms`} small />
          </div>
        </div>
      </div>

      {/* Thresholds */}
      {thresholds.length > 0 && (
        <div className="card">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Threshold Results
          </h2>

          <div className="overflow-x-auto">
            <table className="w-full text-sm" role="table">
              <thead>
                <tr className="text-left text-slate-400 border-b border-slate-700">
                  <th className="pb-2 pr-4">Metric</th>
                  <th className="pb-2 pr-4">Threshold</th>
                  <th className="pb-2 pr-4">Actual</th>
                  <th className="pb-2">Status</th>
                </tr>
              </thead>
              <tbody className="text-slate-200">
                {thresholds.map((t, i) => (
                  <tr key={i} className="border-b border-slate-700/50">
                    <td className="py-2 pr-4 font-mono text-xs">{t.metric}</td>
                    <td className="py-2 pr-4">{t.threshold}</td>
                    <td className="py-2 pr-4">{t.actual}</td>
                    <td className="py-2">
                      {t.evaluated === false ? (
                        <span className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-slate-700 text-slate-300">
                          ↓ In report
                        </span>
                      ) : (
                        <span
                          className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full ${
                            t.passed
                              ? "bg-green-900/40 text-green-400"
                              : "bg-red-900/40 text-red-400"
                          }`}
                        >
                          {t.passed ? "✓ Pass" : "✗ Fail"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {thresholds.some((t) => t.evaluated === false) && (
            <p className="text-xs text-slate-500 mt-3">
              Rows marked <span className="text-slate-400">↓ In report</span> are custom metrics
              the agent evaluates in the detailed analysis below; they don&apos;t affect the banner
              verdict above.
            </p>
          )}
        </div>
      )}

      {/* Anomalies */}
      {anomalies.length > 0 && (
        <div className="card">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <svg className="w-5 h-5 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            Anomalies Detected
          </h2>

          <div className="space-y-3">
            {anomalies.map((anomaly, i) => {
              const severityColors = {
                low: "border-slate-600 bg-slate-800",
                medium: "border-yellow-700 bg-yellow-900/20",
                high: "border-orange-700 bg-orange-900/20",
                critical: "border-red-700 bg-red-900/20",
              };

              const severityBadgeColors = {
                low: "bg-slate-700 text-slate-300",
                medium: "bg-yellow-900/50 text-yellow-400",
                high: "bg-orange-900/50 text-orange-400",
                critical: "bg-red-900/50 text-red-400",
              };

              return (
                <div
                  key={i}
                  className={`border rounded-lg p-4 ${severityColors[anomaly.severity]}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-medium text-slate-200">
                        {anomaly.type}
                      </p>
                      <p className="text-sm text-slate-400 mt-1">
                        {anomaly.description}
                      </p>
                    </div>
                    <span
                      className={`text-xs font-medium px-2 py-0.5 rounded-full flex-shrink-0 ${
                        severityBadgeColors[anomaly.severity]
                      }`}
                    >
                      {anomaly.severity}
                    </span>
                  </div>
                  {anomaly.timestamp && (
                    <p className="text-xs text-slate-500 mt-2">
                      at {anomaly.timestamp}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <div className="card">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
            Recommendations
          </h2>

          <div className="space-y-4">
            {recommendations.map((rec, i) => {
              const priorityColors = {
                low: "text-slate-400",
                medium: "text-yellow-400",
                high: "text-red-400",
              };

              return (
                <div
                  key={i}
                  className="border border-slate-700 rounded-lg p-4 hover:border-slate-600 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs text-slate-500 bg-slate-700 px-2 py-0.5 rounded">
                          {rec.category}
                        </span>
                        <span className={`text-xs font-medium ${priorityColors[rec.priority]}`}>
                          {rec.priority} priority
                        </span>
                      </div>
                      <p className="text-sm font-medium text-slate-200">
                        {rec.title}
                      </p>
                      <p className="text-sm text-slate-400 mt-1">
                        {rec.description}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Detailed Analysis Report (full markdown from the agent) */}
      {report && (
        <div className="card">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            Detailed Analysis
          </h2>
          <MarkdownReport markdown={report} />
        </div>
      )}

      {/* Download */}
      {reportUrl && (
        <div className="flex justify-end">
          <a
            href={reportUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-primary flex items-center gap-2"
            download
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
            Download Full Report
          </a>
        </div>
      )}
    </div>
  );
}

/** Small metric card component */
function MetricCard({
  label,
  value,
  highlight,
  small = false,
}: {
  label: string;
  value: string | number;
  highlight?: "green" | "red";
  small?: boolean;
}) {
  const valueColor = highlight
    ? highlight === "green"
      ? "text-green-400"
      : "text-red-400"
    : "text-white";

  return (
    <div className="bg-slate-900/50 rounded-lg p-3">
      <p className="text-xs text-slate-400 mb-1">{label}</p>
      <p className={`${small ? "text-lg" : "text-xl"} font-semibold ${valueColor}`}>
        {value}
      </p>
    </div>
  );
}
