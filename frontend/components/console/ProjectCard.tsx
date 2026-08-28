import Link from "next/link";

import StageTrack from "@/components/ui/StageTrack";
import { StatusDot } from "@/components/ui/StatusDot";
import { STATUS_LABELS } from "@/lib/api";
import type { Project } from "@/lib/projects";
import { cn, formatRelativeTime } from "@/lib/utils";

/**
 * A project is every run sharing a name — see `lib/projects.ts`. The card shows
 * the latest attempt, because that is what "where is this idea now" means.
 */
export default function ProjectCard({
  project,
  className,
}: {
  project: Project;
  className?: string;
}) {
  const { latest } = project;

  return (
    <Link
      href={`/runs/${latest.id}`}
      className={cn(
        "group flex flex-col gap-3.5 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4",
        "transition-colors hover:border-[var(--line-strong)] hover:bg-[var(--panel-2)]",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-display text-[15px] font-semibold tracking-tight text-[var(--text)]">
            {project.name}
          </p>
          <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-[var(--muted)]">
            {latest.requirement}
          </p>
        </div>
        <StatusDot status={latest.status} className="mt-1.5" />
      </div>

      <StageTrack currentStage={latest.current_stage} status={latest.status} size="sm" />

      <div className="flex items-center gap-2.5 font-mono text-[10.5px] text-[var(--muted-soft)]">
        <span>
          {project.runCount} run{project.runCount === 1 ? "" : "s"}
        </span>
        <Dot />
        <span className="truncate">{STATUS_LABELS[latest.status]}</span>
        {project.bestScore !== null && (
          <>
            <Dot />
            <span>QA {project.bestScore}/10</span>
          </>
        )}
        <Dot />
        <span className="ml-auto shrink-0">{formatRelativeTime(latest.created_at)}</span>
      </div>
    </Link>
  );
}

function Dot() {
  return <span className="text-[var(--line-strong)]">·</span>;
}
