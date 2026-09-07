import Link from "next/link";
import { Download } from "lucide-react";

import StageTrack from "@/components/ui/StageTrack";
import { StatusDot } from "@/components/ui/StatusDot";
import { api, type RunRecord } from "@/lib/api";
import { hasDownload } from "@/lib/deliverables";
import { cn, formatRelativeTime } from "@/lib/utils";

/**
 * One delivered run, presented as the thing it produced.
 *
 * Keyed by `run.id` and never combined with any other run. The heading is
 * `run.name`, which for a completed run is the PRD's `product_name` — a real
 * product name rather than a grouping key.
 */
export default function ProjectCard({
  run,
  className,
}: {
  run: RunRecord;
  className?: string;
}) {
  const downloadable = hasDownload(run);

  return (
    <div
      className={cn(
        "group relative flex flex-col gap-3.5 rounded-xl border border-[var(--line)] bg-[var(--panel)] p-4",
        "transition-colors hover:border-[var(--line-strong)] hover:bg-[var(--panel-2)]",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {/* Stretched link: the whole card opens the run, but the download
              anchor below stays independently clickable. */}
          <Link href={`/runs/${run.id}`} className="after:absolute after:inset-0">
            <p className="truncate font-display text-[15px] font-semibold tracking-tight text-[var(--text)]">
              {run.name?.trim() || "Untitled"}
            </p>
          </Link>
          <p className="mt-1 line-clamp-2 text-[12.5px] leading-relaxed text-[var(--muted)]">
            {run.requirement}
          </p>
        </div>
        <StatusDot status={run.status} className="mt-1.5" />
      </div>

      <StageTrack currentStage={run.current_stage} status={run.status} size="sm" />

      <div className="flex items-center gap-2.5 font-mono text-[10.5px] text-[var(--muted-soft)]">
        <span className="truncate">{run.id.slice(0, 7)}</span>
        {run.qa_score !== null && (
          <>
            <Dot />
            <span>QA {run.qa_score.toFixed(1)}/10</span>
          </>
        )}
        {run.retry_count > 0 && (
          <>
            <Dot />
            <span>
              {run.retry_count} {run.retry_count === 1 ? "retry" : "retries"}
            </span>
          </>
        )}

        <span className="ml-auto flex shrink-0 items-center gap-2.5">
          <span>{formatRelativeTime(run.finished_at || run.created_at)}</span>
          {downloadable && (
            <a
              href={api.zipUrl(run.id)}
              onClick={(event) => event.stopPropagation()}
              className="relative z-10 flex items-center gap-1 rounded px-1.5 py-0.5 text-[var(--muted)] transition-colors hover:bg-[var(--panel)] hover:text-[var(--text)]"
              title="Download the generated project"
            >
              <Download className="h-3 w-3" />
              zip
            </a>
          )}
        </span>
      </div>
    </div>
  );
}

function Dot() {
  return <span className="text-[var(--line-strong)]">·</span>;
}
