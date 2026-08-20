"use client";

import { useState, useCallback, useRef } from "react";
import type {
  TestGenInput,
  TestGenOutput,
  ExecutorOutput,
  AnalysisResult,
  SloThreshold,
} from "@/lib/types";
import { DEFAULT_SLOS } from "@/lib/types";
import {
  generateLoadTest,
  runExecutorTest,
  getTestStatus,
  getAnalysisResult,
} from "@/lib/api";

export interface TestFlowState {
  currentStep: number;
  setCurrentStep: (step: number) => void;
  completedSteps: number[];

  // Step 1 - Generate
  testGenOutput: TestGenOutput | null;
  lastTestGenInput: TestGenInput | null;
  isGenerating: boolean;
  generateError: string | null;
  handleGenerate: (input: TestGenInput) => Promise<void>;

  // Step 2 - Execute
  executorOutput: ExecutorOutput | null;
  isExecuting: boolean;
  executeError: string | null;
  handleExecute: (
    script: string,
    vus: number,
    targetUrl: string,
    targetRps: number
  ) => Promise<void>;

  // Step 3 - Analyze
  analysisResult: AnalysisResult | null;
  isAnalyzing: boolean;
  analyzeError: string | null;
  handleAnalyze: () => Promise<void>;

  // Configurable SLO thresholds (set in Step 2, used by the Step 3 analysis)
  slos: SloThreshold[];
  setSlos: (slos: SloThreshold[]) => void;
}

/** Polling interval for executor status checks (ms) */
const EXECUTOR_POLL_INTERVAL = 15_000;

export function useTestFlow(): TestFlowState {
  const [currentStep, setCurrentStep] = useState(1);
  const [completedSteps, setCompletedSteps] = useState<number[]>([]);

  // Step 1 state
  const [testGenOutput, setTestGenOutput] = useState<TestGenOutput | null>(
    null
  );
  const [lastTestGenInput, setLastTestGenInput] = useState<TestGenInput | null>(
    null
  );
  const [isGenerating, setIsGenerating] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);

  // Step 2 state
  const [executorOutput, setExecutorOutput] = useState<ExecutorOutput | null>(
    null
  );
  const [isExecuting, setIsExecuting] = useState(false);
  const [executeError, setExecuteError] = useState<string | null>(null);

  // Step 3 state
  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(
    null
  );
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);

  // Configurable SLO thresholds (seeded with the previous hardcoded defaults)
  const [slos, setSlos] = useState<SloThreshold[]>(DEFAULT_SLOS);

  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  const pollStartTimeRef = useRef<number>(0);
  // Target URL from Step 2 — passed to the analysis call so the Analysis agent
  // can scope X-Ray traces / CloudWatch metrics to the app under test.
  const targetUrlRef = useRef<string>("");

  const markStepCompleted = useCallback((step: number) => {
    setCompletedSteps((prev) => (prev.includes(step) ? prev : [...prev, step]));
  }, []);

  const handleGenerate = useCallback(async (input: TestGenInput) => {
    setIsGenerating(true);
    setGenerateError(null);
    setLastTestGenInput(input);

    try {
      const result = await generateLoadTest(
        input.spec,
        input.prompt,
        input.dependencies,
        input.records,
        input.context
      );
      setTestGenOutput(result);
      markStepCompleted(1);
      setCurrentStep(2);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to generate load test";
      setGenerateError(message);
    } finally {
      setIsGenerating(false);
    }
  }, [markStepCompleted]);

  const pollTestStatus = useCallback(
    async (testId: string) => {
      try {
        const status = await getTestStatus(testId);
        setExecutorOutput(status);

        if (status.status === "complete") {
          // Stop polling
          if (pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
          setIsExecuting(false);
          markStepCompleted(2);
        } else if (status.status === "failed") {
          if (pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
          setIsExecuting(false);
          setExecuteError("Test execution failed");
        }
      } catch (err) {
        // Polling errors are non-fatal, keep trying
        console.error("Polling error:", err);
      }
    },
    [markStepCompleted]
  );

  const handleExecute = useCallback(
    async (
      script: string,
      vus: number,
      targetUrl: string,
      targetRps: number
    ) => {
      setIsExecuting(true);
      setExecuteError(null);
      setExecutorOutput(null);
      setAnalysisResult(null);
      setAnalyzeError(null);
      targetUrlRef.current = targetUrl;

      try {
        const result = await runExecutorTest(script, vus, "", targetUrl, targetRps);
        setExecutorOutput(result);

        // Start polling for status updates
        if (result.testId && result.status !== "complete") {
          pollStartTimeRef.current = Date.now();
          pollingRef.current = setInterval(() => {
            pollTestStatus(result.testId);
          }, EXECUTOR_POLL_INTERVAL);
        } else if (result.status === "complete") {
          setIsExecuting(false);
          markStepCompleted(2);
        }
      } catch (err) {
        const message =
          err instanceof Error
            ? err.message
            : "Failed to start performance test";
        setExecuteError(message);
        setIsExecuting(false);
      }
    },
    [pollTestStatus, markStepCompleted]
  );

  /** Explicitly triggered by user after reviewing Step 2 results */
  const handleAnalyze = useCallback(async () => {
    if (!executorOutput?.testId) return;

    setIsAnalyzing(true);
    setAnalyzeError(null);
    setCurrentStep(3);

    try {
      const analysis = await getAnalysisResult(
        executorOutput.testId,
        targetUrlRef.current,
        slos
      );
      setAnalysisResult(analysis);
      markStepCompleted(3);
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Failed to fetch analysis results";
      setAnalyzeError(message);
    } finally {
      setIsAnalyzing(false);
    }
  }, [executorOutput, markStepCompleted, slos]);

  return {
    currentStep,
    setCurrentStep,
    completedSteps,
    testGenOutput,
    lastTestGenInput,
    isGenerating,
    generateError,
    handleGenerate,
    executorOutput,
    isExecuting,
    executeError,
    handleExecute,
    analysisResult,
    isAnalyzing,
    analyzeError,
    handleAnalyze,
    slos,
    setSlos,
  };
}
