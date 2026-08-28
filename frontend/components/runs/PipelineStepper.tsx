import { Check, Loader2, Slash } from "lucide-react";

import { cn } from "@/lib/utils";
import type { AgentStatus, StageProgress } from "@/lib/api";

/**
 * Where the run is, from the authoritative `StageProgress[]`.
 *
 * `StageTrack` derives position from `current_stage` and is right for a list
 * row; this uses the per-stage statuses the API actually returns, which is
 * strictly better when they are available — a stage can be FAILED while a later
 * one is PENDING, and only this shape can say so.
 *
 * Status is fill and mark, not hue: done is filled, running is ringed and
 * moving, failed is struck, pending is a dashed outline holding its number.
 */
export default function PipelineStepper({ stages }: { stages: StageProgress[] }) {
  if (stages.length === 0) return null;

  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
      <div className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
        Pipeline
      </div>

      <ol className="mt-4 flex items-start gap-1 overflow-x-auto pb-1">
        {stages.map((stage, index) => (
          <li key={stage.id} className="flex shrink-0 items-start gap-1">
            <div className="flex w-[86px] flex-col items-center gap-2">
              <Mark status={stage.status} index={index} />
              <span
                className={cn(
                  "text-center text-[11px] leading-tight",
                  stage.status === "PENDING"
                    ? "text-[var(--muted-soft)]"
                    : stage.status === "IN_PROGRESS"
                      ? "font-medium text-[var(--text)]"
                      : "text-[var(--muted)]",
                )}
              >
                {stage.label}
              </span>
            </div>

            {index < stages.length - 1 && (
              <div
                className={cn(
                  "mt-4 h-px w-4 shrink-0",
                  stage.status === "COMPLETED" ? "bg-[var(--muted)]" : "bg-[var(--line-strong)]",
                )}
              />
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function Mark({ status, index }: { status: AgentStatus; index: number }) {
  return (
    <span
      className={cn(
        "flex h-8 w-8 items-center justify-center rounded-full border text-[11px] font-semibold transition-colors",
        status === "COMPLETED" &&
          "border-transparent bg-[var(--invert-bg)] text-[var(--invert-text)]",
        status === "IN_PROGRESS" && "border-[1.5px] border-[var(--text)] text-[var(--text)]",
        status === "FAILED" && "border-[1.5px] border-[var(--text)] text-[var(--text)]",
        status === "PENDING" &&
          "border-dashed border-[var(--line-strong)] text-[var(--muted-soft)]",
      )}
    >
      {status === "COMPLETED" && <Check className="h-4 w-4" strokeWidth={2.6} />}
      {status === "IN_PROGRESS" && <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.2} />}
      {status === "FAILED" && <Slash className="h-4 w-4" strokeWidth={2.4} />}
      {status === "PENDING" && index + 1}
    </span>
  );
}
