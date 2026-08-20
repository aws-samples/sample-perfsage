"use client";

interface StepperProps {
  currentStep: number;
  onStepClick?: (step: number) => void;
  /** Steps that have been completed (user can navigate back to them) */
  completedSteps?: number[];
}

const steps = [
  {
    number: 1,
    label: "Generate",
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4" />
      </svg>
    ),
  },
  {
    number: 2,
    label: "Execute",
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  {
    number: 3,
    label: "Analyze",
    icon: (
      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
    ),
  },
];

export function Stepper({ currentStep, onStepClick, completedSteps = [] }: StepperProps) {
  return (
    <nav aria-label="Progress" className="card">
      <ol className="flex items-center justify-between" role="list">
        {steps.map((step, index) => {
          const isActive = step.number === currentStep;
          const isCompleted = completedSteps.includes(step.number) || step.number < currentStep;
          const isClickable = onStepClick && (isCompleted || isActive);

          return (
            <li key={step.number} className="flex items-center flex-1">
              <button
                type="button"
                onClick={() => isClickable && onStepClick?.(step.number)}
                disabled={!isClickable}
                className={`flex items-center gap-3 ${isClickable ? "cursor-pointer hover:opacity-80" : "cursor-default"}`}
              >
                <div
                  className={`
                    flex items-center justify-center w-10 h-10 rounded-full border-2 transition-all duration-300
                    ${isCompleted
                      ? "bg-green-600 border-green-600 text-white"
                      : isActive
                        ? "bg-blue-600 border-blue-600 text-white shadow-lg shadow-blue-600/30"
                        : "border-slate-600 text-slate-400"
                    }
                  `}
                  aria-current={isActive ? "step" : undefined}
                >
                  {isCompleted && !isActive ? (
                    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    step.icon
                  )}
                </div>
                <span
                  className={`text-sm font-medium hidden sm:block ${
                    isActive
                      ? "text-blue-400"
                      : isCompleted
                        ? "text-green-400"
                        : "text-slate-400"
                  }`}
                >
                  {step.label}
                </span>
              </button>

              {index < steps.length - 1 && (
                <div
                  className={`flex-1 h-0.5 mx-4 rounded transition-colors duration-300 ${
                    isCompleted ? "bg-green-600" : "bg-slate-700"
                  }`}
                  aria-hidden="true"
                />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
