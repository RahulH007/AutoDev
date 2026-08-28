import { cn } from "@/lib/utils";
import { STAGES, stageIndex } from "@/lib/stages";
import type { RunStatus } from "@/lib/api";

/**
 * Six segments, one per pipeline stage, filled up to where a run has reached.
 *
 * This is the console's one repeated device. It answers the question everything
 * else only hints at — how far did it get — and it works without colour, which
 * a status badge alone does not.
 */

type Size = "sm" | "md" | "lg";

const GAP: Record<Size, string> = {
  sm: "gap-[3px]",
  md: "gap-1",
  lg: "gap-1.5",
};

const HEIGHT: Record<Size, string> = {
  sm: "h-[3px]",
  md: "h-1",
  lg: "h-1.5",
};

export interface StageTrackProps {
  currentStage?: string | null;
  status?: RunStatus;
  size?: Size;
  /** Render the stage abbreviations under the track. */
  withLabels?: boolean;
  /** No run yet — the track shows what *would* happen, at low contrast. */
  idle?: boolean;
  /** "invert" for use on the inverted CTA, where the page tokens would vanish. */
  tone?: "default" | "invert";
  className?: string;
}

export default function StageTrack({
  currentStage,
  status,
  size = "md",
  withLabels = false,
  idle = false,
  tone = "default",
  className,
}: StageTrackProps) {
  const reached = idle ? -1 : stageIndex(currentStage);
  const failed = status === "failed";
  const done = status === "completed";

  return (
    <div className={cn("w-full", tone === "invert" && "track-invert", className)}>
      <div
        className={cn("flex w-full", GAP[size])}
        role="img"
        aria-label={trackLabel(reached, status, idle)}
      >
        {STAGES.map((stage, index) => (
          <span
            key={stage.id}
            data-state={segmentState(index, reached, { failed, done, idle })}
            className={cn("track-seg flex-1", HEIGHT[size])}
          />
        ))}
      </div>

      {withLabels && (
        <div className={cn("flex w-full mt-2", GAP[size])} aria-hidden="true">
          {STAGES.map((stage, index) => (
            <span
              key={stage.id}
              className={cn(
                "flex-1 font-mono text-[9.5px] uppercase tracking-[0.1em] transition-colors",
                tone === "invert"
                  ? "text-[var(--invert-text)]"
                  : index === reached && !done
                    ? "text-[var(--text)]"
                    : index <= reached || done
                      ? "text-[var(--muted)]"
                      : "text-[var(--muted-soft)]",
              )}
            >
              {stage.short}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function segmentState(
  index: number,
  reached: number,
  flags: { failed: boolean; done: boolean; idle: boolean },
): "pending" | "done" | "current" | "failed" {
  if (flags.idle) return "pending";
  if (flags.done) return "done";
  if (index < reached) return "done";
  if (index === reached) return flags.failed ? "failed" : "current";
  return "pending";
}

function trackLabel(reached: number, status: RunStatus | undefined, idle: boolean): string {
  if (idle) return "Pipeline stages, not started";
  if (status === "completed") return "All six stages completed";
  if (reached < 0) return "Pipeline not started";
  const stage = STAGES[reached];
  const position = `stage ${reached + 1} of ${STAGES.length}`;
  return status === "failed"
    ? `Failed at ${stage.label}, ${position}`
    : `At ${stage.label}, ${position}`;
}
