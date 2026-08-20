"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders the analysis agent's markdown report with a dark theme.
 * Component overrides give us Tailwind styling without the typography plugin.
 */
export function MarkdownReport({ markdown }: { markdown: string }) {
  return (
    <div className="text-sm text-slate-300 leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="text-xl font-bold text-white mt-5 mb-2">{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-lg font-semibold text-white mt-5 mb-2">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-base font-semibold text-slate-100 mt-4 mb-1.5">{children}</h3>
          ),
          p: ({ children }) => <p className="my-2">{children}</p>,
          ul: ({ children }) => (
            <ul className="list-disc pl-5 my-2 space-y-1">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-5 my-2 space-y-1">{children}</ol>
          ),
          li: ({ children }) => <li className="text-slate-300">{children}</li>,
          strong: ({ children }) => (
            <strong className="font-semibold text-white">{children}</strong>
          ),
          em: ({ children }) => <em className="italic text-slate-300">{children}</em>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-blue-400 underline">
              {children}
            </a>
          ),
          code: ({ children }) => (
            <code className="bg-slate-800 px-1.5 py-0.5 rounded text-xs font-mono text-blue-300">
              {children}
            </code>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-4 border-slate-600 pl-3 my-3 italic text-slate-400">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="border-slate-700 my-4" />,
          table: ({ children }) => (
            <div className="overflow-x-auto my-3">
              <table className="w-full text-sm border-collapse">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead>{children}</thead>,
          th: ({ children }) => (
            <th className="text-left text-slate-400 border-b border-slate-700 pb-2 pr-4 font-medium">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="py-2 pr-4 border-b border-slate-700/50 text-slate-200 align-top">
              {children}
            </td>
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
