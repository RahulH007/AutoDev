import { cn } from "@/lib/utils";
import { STATUS_LABELS, type RunStatus } from "@/lib/api";

/**
 * Status without hue.
 *
 * Colour is the usual way to separate seven run states, and it is unavailable
 * here. Fill, ring and motion carry it instead: a running dot pulses, a review
 * gate is a hollow ring (nothing is moving, something is expected of you), a
 * completed run is solid, a failed one is struck through. The label is always
 * present, so the mark never has to carry the meaning alone.
 */

type Shape = "solid" | "ring" | "pulse" | "struck" | "dotted";

const SHAPE: Record<RunStatus, Shape> = {
  queued: "dotted",
  running: "pulse",
  awaiting_pm_review: "ring",
  awaiting_architecture_review: "ring",
  completed: "solid",
  failed: "struck",
  cancelled: "ring",
};

export function StatusDot({ status, className }: { status: RunStatus; className?: string }) {
  const shape = SHAPE[status] ?? "dotted";

  return (
    <span className={cn("relative inline-flex h-2.5 w-2.5 shrink-0", className)}>
      {shape === "pulse" && (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--text)] opacity-40" />
      )}
      <span
        className={cn(
          "relative inline-flex h-2.5 w-2.5 rounded-full",
          shape === "solid" && "bg-[var(--text)]",
          shape === "pulse" && "bg-[var(--text)]",
          shape === "ring" && "border-[1.5px] border-[var(--text)]",
          shape === "dotted" && "border border-dashed border-[var(--muted)]",
          shape === "struck" && "border-[1.5px] border-[var(--text)]",
        )}
      >
        {shape === "struck" && (
          <span className="absolute left-1/2 top-1/2 h-[1.5px] w-[130%] -translate-x-1/2 -translate-y-1/2 rotate-45 bg-[var(--text)]" />
        )}
      </span>
    </span>
  );
}

export default function StatusPill({
  status,
  className,
}: {
  status: RunStatus;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 whitespace-nowrap rounded-full border border-[var(--line)]",
        "bg-[var(--panel-2)] py-1 pl-2 pr-2.5 text-[11px] font-medium text-[var(--text)]",
        className,
      )}
    >
      <StatusDot status={status} />
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}
