import Link from "next/link";
import { ChevronRight } from "lucide-react";

import StageTrack from "@/components/ui/StageTrack";
import { StatusDot } from "@/components/ui/StatusDot";
import { STATUS_LABELS, type RunRecord } from "@/lib/api";
import { stageLabel } from "@/lib/stages";
import { cn, formatRelativeTime } from "@/lib/utils";

/**
 * One run, at a glance.
 *
 * The track carries how far it got, the status mark carries what it is doing
 * now, and the stage name says it in words. Nothing here is derived from
 * anything the API does not return.
 */
export default function RunRow({
  run,
  className,
}: {
  run: RunRecord;
  className?: string;
}) {
  return (
    <Link
      href={`/runs/${run.id}`}
      className={cn(
        "group flex items-center gap-4 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-3.5",
        "transition-colors hover:border-[var(--line-strong)] hover:bg-[var(--panel-2)]",
        className,
      )}
    >
      <StatusDot status={run.status} className="mt-0.5" />

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2.5">
          <p className="truncate text-[14px] font-medium text-[var(--text)]">
            {run.name?.trim() || "Untitled"}
          </p>
          <span className="shrink-0 font-mono text-[10.5px] text-[var(--muted-soft)]">
            {run.id.slice(0, 7)}
          </span>
        </div>
        <p className="mt-0.5 truncate text-[12.5px] text-[var(--muted)]">
          {run.requirement}
        </p>
      </div>

      <div className="hidden w-[150px] shrink-0 sm:block">
        <StageTrack currentStage={run.current_stage} status={run.status} size="sm" />
        <p className="mt-1.5 truncate font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--muted-soft)]">
          {run.status === "completed" ? "All stages" : stageLabel(run.current_stage)}
        </p>
      </div>

      <div className="hidden shrink-0 text-right md:block">
        <p className="text-[12px] text-[var(--muted)]">{STATUS_LABELS[run.status]}</p>
        <p className="mt-0.5 font-mono text-[10.5px] text-[var(--muted-soft)]">
          {formatRelativeTime(run.created_at)}
        </p>
      </div>

      <ChevronRight className="h-4 w-4 shrink-0 text-[var(--muted-soft)] transition-transform group-hover:translate-x-0.5" />
    </Link>
  );
}
