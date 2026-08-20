"use client";

import { Stepper } from "@/components/Stepper";
import { Step1TestGen } from "@/components/Step1TestGen";
import { Step2Executor } from "@/components/Step2Executor";
import { Step3Analysis } from "@/components/Step3Analysis";
import { useTestFlow } from "@/hooks/useTestFlow";

export default function Home() {
  const flow = useTestFlow();

  return (
    <div className="space-y-8">
      <Stepper
        currentStep={flow.currentStep}
        onStepClick={flow.setCurrentStep}
        completedSteps={flow.completedSteps}
      />

      {flow.currentStep === 1 && (
        <Step1TestGen
          onGenerate={flow.handleGenerate}
          isLoading={flow.isGenerating}
          error={flow.generateError}
          initialInput={flow.lastTestGenInput}
        />
      )}

      {flow.currentStep === 2 && flow.testGenOutput && (
        <Step2Executor
          testGenOutput={flow.testGenOutput}
          onExecute={flow.handleExecute}
          isLoading={flow.isExecuting}
          executorOutput={flow.executorOutput}
          error={flow.executeError}
          onAnalyze={flow.handleAnalyze}
          slos={flow.slos}
          onSlosChange={flow.setSlos}
        />
      )}

      {flow.currentStep === 3 && (
        <Step3Analysis
          analysisResult={flow.analysisResult}
          isLoading={flow.isAnalyzing}
          error={flow.analyzeError}
        />
      )}
    </div>
  );
}
