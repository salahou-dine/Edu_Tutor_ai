import { Check, Loader2 } from "lucide-react";

import { STEP_DONE_LABELS, STEP_LABELS } from "../lib/labels";

export interface ProgressStep {
  key: string; // planning | tutor | idp | content | compose
  state: "pending" | "running" | "done";
}

/**
 * Timeline de progression du flux SSE : l'étudiant voit les agents avancer au
 * lieu d'un spinner muet de plusieurs minutes.
 */
export function ProgressTimeline({ steps }: { steps: ProgressStep[] }) {
  if (steps.length === 0) return null;
  return (
    <div className="glass px-5 py-4 flex flex-col gap-2.5 max-w-md">
      {steps.map((step) => (
        <div key={step.key} className="flex items-center gap-3 text-sm">
          {step.state === "done" ? (
            <Check size={15} className="shrink-0 text-accent-soft" />
          ) : step.state === "running" ? (
            <Loader2 size={15} className="shrink-0 animate-spin text-accent-soft" />
          ) : (
            <span className="shrink-0 w-[15px] h-[15px] rounded-full border border-surface-border" />
          )}
          <span className={step.state === "pending" ? "text-muted" : "text-ink"}>
            {step.state === "done"
              ? STEP_DONE_LABELS[step.key] ?? step.key
              : STEP_LABELS[step.key] ?? step.key}
          </span>
        </div>
      ))}
    </div>
  );
}
