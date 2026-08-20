"use client";

import { useState, useCallback } from "react";
import type { TestGenInput, Dependency } from "@/lib/types";

interface Step1TestGenProps {
  onGenerate: (input: TestGenInput) => Promise<void>;
  isLoading: boolean;
  error: string | null;
  /** Pre-fill form with values from the last submission (for "go back and tweak") */
  initialInput?: TestGenInput | null;
}

export function Step1TestGen({ onGenerate, isLoading, error, initialInput }: Step1TestGenProps) {
  const [spec, setSpec] = useState(initialInput?.spec || "");
  const [specInputMode, setSpecInputMode] = useState<"text" | "file">("text");
  const [prompt, setPrompt] = useState(initialInput?.prompt || "");
  const [dependencies, setDependencies] = useState<Dependency[]>(
    initialInput?.dependencies?.length
      ? initialInput.dependencies
      : [{ parent: "", child: "", via: "" }]
  );
  const [records, setRecords] = useState(initialInput?.records || "");
  const [context, setContext] = useState(initialInput?.context || "");

  const handleFileUpload = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

      const reader = new FileReader();
      reader.onload = (event) => {
        const content = event.target?.result as string;
        setSpec(content);
      };
      reader.readAsText(file);
    },
    []
  );

  const addDependency = () => {
    setDependencies([...dependencies, { parent: "", child: "", via: "" }]);
  };

  const removeDependency = (index: number) => {
    setDependencies(dependencies.filter((_, i) => i !== index));
  };

  const updateDependency = (
    index: number,
    field: keyof Dependency,
    value: string
  ) => {
    const updated = [...dependencies];
    updated[index] = { ...updated[index], [field]: value };
    setDependencies(updated);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const validDeps = dependencies.filter(
      (d) => d.parent && d.child && d.via
    );
    await onGenerate({
      spec,
      prompt,
      dependencies: validDeps,
      records,
      context,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
          </svg>
          OpenAPI Specification
        </h2>

        <div className="flex gap-2 mb-3">
          <button
            type="button"
            onClick={() => setSpecInputMode("text")}
            className={`text-sm px-3 py-1.5 rounded-md transition-colors ${
              specInputMode === "text"
                ? "bg-blue-600 text-white"
                : "bg-slate-700 text-slate-300 hover:bg-slate-600"
            }`}
          >
            Paste Text
          </button>
          <button
            type="button"
            onClick={() => setSpecInputMode("file")}
            className={`text-sm px-3 py-1.5 rounded-md transition-colors ${
              specInputMode === "file"
                ? "bg-blue-600 text-white"
                : "bg-slate-700 text-slate-300 hover:bg-slate-600"
            }`}
          >
            Upload File
          </button>
        </div>

        {specInputMode === "text" ? (
          <textarea
            value={spec}
            onChange={(e) => setSpec(e.target.value)}
            placeholder="Paste your OpenAPI specification (YAML or JSON)..."
            className="textarea-field font-mono text-sm"
            rows={8}
            required
            aria-label="OpenAPI specification"
          />
        ) : (
          <div className="border-2 border-dashed border-slate-600 rounded-lg p-8 text-center hover:border-blue-500 transition-colors">
            <input
              type="file"
              accept=".yaml,.yml,.json"
              onChange={handleFileUpload}
              className="hidden"
              id="spec-upload"
              aria-label="Upload OpenAPI specification file"
            />
            <label
              htmlFor="spec-upload"
              className="cursor-pointer flex flex-col items-center gap-2"
            >
              <svg className="w-10 h-10 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              <span className="text-sm text-slate-300">
                Click to upload YAML/JSON file
              </span>
              <span className="text-xs text-slate-500">
                Supports .yaml, .yml, .json
              </span>
            </label>
            {spec && (
              <p className="mt-3 text-sm text-green-400">
                ✓ File loaded ({spec.length} characters)
              </p>
            )}
          </div>
        )}
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
          </svg>
          Test Generation Prompt
        </h2>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe what you want to test. E.g., 'Generate a load test that simulates 100 concurrent users performing CRUD operations on the Orders API with realistic think times...'"
          className="textarea-field"
          rows={4}
          required
          aria-label="Natural language prompt for test generation"
        />
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
          </svg>
          Resource Dependencies
        </h2>
        <p className="text-sm text-slate-400 mb-4">
          Define parent-child relationships between API resources for ordered test execution.
        </p>

        <div className="space-y-3">
          {dependencies.map((dep, index) => (
            <div key={index} className="flex flex-col sm:flex-row gap-2 items-start sm:items-center">
              <input
                type="text"
                value={dep.parent}
                onChange={(e) => updateDependency(index, "parent", e.target.value)}
                placeholder="Parent resource"
                className="input-field flex-1"
                aria-label={`Dependency ${index + 1} parent resource`}
              />
              <span className="text-slate-500 text-sm hidden sm:block">→</span>
              <input
                type="text"
                value={dep.child}
                onChange={(e) => updateDependency(index, "child", e.target.value)}
                placeholder="Child resource"
                className="input-field flex-1"
                aria-label={`Dependency ${index + 1} child resource`}
              />
              <span className="text-slate-500 text-sm hidden sm:block">via</span>
              <input
                type="text"
                value={dep.via}
                onChange={(e) => updateDependency(index, "via", e.target.value)}
                placeholder="Field (e.g., parentId)"
                className="input-field flex-1"
                aria-label={`Dependency ${index + 1} linking field`}
              />
              <button
                type="button"
                onClick={() => removeDependency(index)}
                className="text-red-400 hover:text-red-300 p-2 rounded-lg hover:bg-slate-700 transition-colors"
                aria-label={`Remove dependency ${index + 1}`}
                disabled={dependencies.length === 1}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
          ))}
        </div>

        <button
          type="button"
          onClick={addDependency}
          className="mt-3 text-sm text-blue-400 hover:text-blue-300 flex items-center gap-1 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
          </svg>
          Add dependency
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4" />
            </svg>
            Records
          </h2>
          <p className="text-sm text-slate-400 mb-3">
            Specify resource counts, one per line (e.g., <code className="text-blue-300">users: 1000</code>)
          </p>
          <textarea
            value={records}
            onChange={(e) => setRecords(e.target.value)}
            placeholder={"users: 1000\norders: 5000\nproducts: 200"}
            className="textarea-field font-mono text-sm"
            rows={5}
            aria-label="Resource record counts"
          />
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <svg className="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Business Context
          </h2>
          <p className="text-sm text-slate-400 mb-3">
            Provide additional context about your API and testing goals.
          </p>
          <textarea
            value={context}
            onChange={(e) => setContext(e.target.value)}
            placeholder="E.g., This is an e-commerce API handling Black Friday traffic. Peak load expected at 10x normal. Most critical paths are checkout and payment..."
            className="textarea-field"
            rows={5}
            aria-label="Business context"
          />
        </div>
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 flex items-start gap-3" role="alert">
          <svg className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <div>
            <p className="text-sm font-medium text-red-300">Generation Failed</p>
            <p className="text-sm text-red-400 mt-1">{error}</p>
          </div>
        </div>
      )}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={isLoading || !spec || !prompt}
          className="btn-primary flex items-center gap-2"
        >
          {isLoading ? (
            <>
              <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24" aria-hidden="true">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              Generating...
            </>
          ) : (
            <>
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
              Generate Load Test
            </>
          )}
        </button>
      </div>
    </form>
  );
}
