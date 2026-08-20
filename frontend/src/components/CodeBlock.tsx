"use client";

import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { useState } from "react";

interface CodeBlockProps {
  code: string;
  language?: string;
  title?: string;
  collapsible?: boolean;
  maxHeight?: string;
}

export function CodeBlock({
  code,
  language = "javascript",
  title,
  collapsible = false,
  maxHeight = "500px",
}: CodeBlockProps) {
  const [isCollapsed, setIsCollapsed] = useState(collapsible);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for older browsers
      const textarea = document.createElement("textarea");
      textarea.value = code;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="rounded-lg border border-slate-700 overflow-hidden">
      {title && (
        <div className="flex items-center justify-between bg-slate-800 px-4 py-2 border-b border-slate-700">
          <div className="flex items-center gap-2">
            {collapsible && (
              <button
                onClick={() => setIsCollapsed(!isCollapsed)}
                className="text-slate-400 hover:text-slate-200 transition-colors"
                aria-expanded={!isCollapsed}
                aria-label={isCollapsed ? "Expand code" : "Collapse code"}
              >
                <svg
                  className={`w-4 h-4 transition-transform ${isCollapsed ? "" : "rotate-90"}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>
            )}
            <span className="text-sm font-medium text-slate-300">{title}</span>
            <span className="text-xs text-slate-500 bg-slate-700/50 px-2 py-0.5 rounded">
              {language}
            </span>
          </div>
          <button
            onClick={handleCopy}
            className="text-xs text-slate-400 hover:text-slate-200 transition-colors px-2 py-1 rounded hover:bg-slate-700"
            aria-label="Copy code"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>
      )}

      {!isCollapsed && (
        <div style={{ maxHeight }} className="overflow-auto">
          <SyntaxHighlighter
            language={language}
            style={oneDark}
            customStyle={{
              margin: 0,
              borderRadius: 0,
              background: "#1e293b",
              fontSize: "0.8125rem",
            }}
            showLineNumbers
          >
            {code}
          </SyntaxHighlighter>
        </div>
      )}
    </div>
  );
}
