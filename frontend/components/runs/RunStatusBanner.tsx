import Link from "next/link";
import { AlertTriangle, Check, Download, Loader2, PauseCircle, Slash } from "lucide-react";

import StageTrack from "@/components/ui/StageTrack";
import { api, type RunDetail, type RunEvent, type RunRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * What the run is doing, said plainly.
 *
 * A run spends most of its life with every artifact panel empty — the brief
 * takes a model call, the code takes several. Without this the page reads as
 * broken rather than busy.
 *
 * Everything here is measured, never estimated. The stage comes from
 * `RunDetail.stages`, which the API computes from the graph's own per-stage
 * status; the position is that array's index; the activity line is the last
 * event off the existing SSE stream. There is deliberately no percentage and no
 * time remaining, because the pipeline knows neither — a stage can retry three
 * times or wait out a rate limit, and any number here would be invented.
 */
export default function RunStatusBanner({
  run,
  stages,
  latestEvent,
  hasZip,
  onSeeReview,
}: {
  run: RunRecord;
  stages: RunDetail["stages"];
  latestEvent: RunEvent | null;
  hasZip: boolean;
  onSeeReview?: () => void;
}) {
  const active = run.status === "queued" || run.status === "running";
  const awaitingReview =
    run.status === "awaiting_pm_review" || run.status === "awaiting_architecture_review";

  if (active) {
    return <Building run={run} stages={stages} latestEvent={latestEvent} />;
  }
  if (awaitingReview) {
    return <AwaitingReview run={run} stages={stages} onSeeReview={onSeeReview} />;
  }
  if (run.status === "completed") {
    return <Completed run={run} hasZip={hasZip} />;
  }
  if (run.status === "failed") {
    return <Failed run={run} stages={stages} />;
  }
  if (run.status === "cancelled") {
    return (
      <Frame mark={<Slash className="h-4 w-4" strokeWidth={2.2} />} title="Run cancelled">
        <Body>
          This run was stopped before it finished. Anything it produced before that point is
          still below.
        </Body>
      </Frame>
    );
  }
  return null;
}

/* ── Building ────────────────────────────────────────────────── */

function Building({
  run,
  stages,
  latestEvent,
}: {
  run: RunRecord;
  stages: RunDetail["stages"];
  latestEvent: RunEvent | null;
}) {
  const currentIndex = stages.findIndex((stage) => stage.status === "IN_PROGRESS");
  const current = currentIndex >= 0 ? stages[currentIndex] : null;
  const position = currentIndex >= 0 ? currentIndex + 1 : null;

  return (
    <Frame
      mark={<Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.2} />}
      title="Building your POC"
      emphasis
    >
      <p className="mt-1 text-[14px] leading-relaxed text-[var(--text)]">
        {current ? (
          <>
            <span className="font-medium">{current.label}</span>
            {position !== null && (
              <span className="text-[var(--muted)]">
                {" "}
                · stage {position} of {stages.length}
              </span>
            )}
          </>
        ) : run.status === "queued" ? (
          "Queued — the pipeline is about to start."
        ) : (
          "Starting the pipeline…"
        )}
      </p>

      <StageTrack
        currentStage={run.current_stage}
        status={run.status}
        size="md"
        withLabels
        className="mt-4"
      />

      <Activity event={latestEvent} />
    </Frame>
  );
}

/**
 * The newest line off the SSE stream, so the banner shows real movement rather
 * than a spinner that could equally mean "hung".
 */
function Activity({ event }: { event: RunEvent | null }) {
  if (!event) return null;

  return (
    <div className="mt-4 flex items-start gap-2.5 border-t border-[var(--line)] pt-3.5">
      <span className="mt-[5px] h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[var(--text)]" />
      <p className="min-w-0 font-mono text-[11px] leading-relaxed text-[var(--muted)]">
        <span className="text-[var(--muted-soft)]">
          {event.stage ? `${event.stage} · ` : ""}
        </span>
        {event.message}
      </p>
    </div>
  );
}

/* ── Awaiting review ─────────────────────────────────────────── */

function AwaitingReview({
  run,
  stages,
  onSeeReview,
}: {
  run: RunRecord;
  stages: RunDetail["stages"];
  onSeeReview?: () => void;
}) {
  const isPm = run.status === "awaiting_pm_review";
  const artifact = isPm ? "product brief" : "architecture";
  const done = stages.filter((stage) => stage.status === "COMPLETED").length;

  return (
    <Frame
      mark={<PauseCircle className="h-4 w-4" strokeWidth={2.1} />}
      title="Paused for your review"
      emphasis
    >
      <Body>
        The {artifact} is ready and nothing will run until you say so. Approve it to continue, or
        send notes and the agent reworks its own output.
      </Body>

      <StageTrack
        currentStage={run.current_stage}
        status={run.status}
        size="md"
        withLabels
        className="mt-4"
      />

      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-[var(--line)] pt-3.5">
        {onSeeReview && (
          <button
            onClick={onSeeReview}
            className="inline-flex items-center gap-2 rounded-lg bg-[var(--invert-bg)] px-4 py-2 text-[13px] font-semibold text-[var(--invert-text)] transition-opacity hover:opacity-90"
          >
            Review the {artifact}
          </button>
        )}
        <span className="font-mono text-[10.5px] uppercase tracking-[0.1em] text-[var(--muted-soft)]">
          {done} of {stages.length} stages done
        </span>
      </div>
    </Frame>
  );
}

/* ── Terminal states ─────────────────────────────────────────── */

function Completed({ run, hasZip }: { run: RunRecord; hasZip: boolean }) {
  return (
    <Frame mark={<Check className="h-4 w-4" strokeWidth={2.6} />} title="Your POC is ready" filled>
      <Body>
        Every stage finished and the generated code was compiled and tested. The brief,
        architecture and verification results are below.
      </Body>

      {hasZip && (
        <a
          href={api.zipUrl(run.id)}
          className="mt-4 inline-flex items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--invert-text)_25%,transparent)] px-4 py-2 text-[13px] font-semibold text-[var(--invert-text)] transition-colors hover:bg-[color-mix(in_srgb,var(--invert-text)_10%,transparent)]"
        >
          <Download className="h-3.5 w-3.5" />
          Download the project
        </a>
      )}
    </Frame>
  );
}

function Failed({ run, stages }: { run: RunRecord; stages: RunDetail["stages"] }) {
  const failedStage = stages.find((stage) => stage.status === "FAILED");
  const done = stages.filter((stage) => stage.status === "COMPLETED").length;

  return (
    <Frame
      mark={<AlertTriangle className="h-4 w-4" strokeWidth={2.1} />}
      title="This run did not finish"
      emphasis
    >
      <Body>
        {failedStage
          ? `It stopped at ${failedStage.label.toLowerCase()}, after completing ${done} of ${stages.length} stages.`
          : `It stopped after completing ${done} of ${stages.length} stages.`}{" "}
        Whatever was produced before that point is still below.
      </Body>

      {run.error && (
        <p className="mt-3 break-words rounded-md border border-[var(--line)] bg-[var(--panel-2)] p-3 font-mono text-[11px] leading-relaxed text-[var(--muted)]">
          {run.error}
        </p>
      )}

      <p className="mt-3.5 text-[12.5px] text-[var(--muted)]">
        <Link
          href="/new"
          className="text-[var(--text)] underline-offset-2 transition-colors hover:underline"
        >
          Start a new run
        </Link>{" "}
        with a more specific description, or read the log below for what went wrong.
      </p>
    </Frame>
  );
}

/* ── Shell ───────────────────────────────────────────────────── */

function Frame({
  mark,
  title,
  children,
  emphasis,
  filled,
}: {
  mark: React.ReactNode;
  title: string;
  children: React.ReactNode;
  /** A left rule: this state wants attention but is not the page's one action. */
  emphasis?: boolean;
  /** Full inversion, reserved for the finished state. */
  filled?: boolean;
}) {
  return (
    <section
      className={cn(
        "rounded-xl px-5 py-4",
        filled
          ? "bg-[var(--invert-bg)] text-[var(--invert-text)]"
          : cn(
              "border border-[var(--line)] bg-[var(--panel)]",
              emphasis && "border-l-2 border-l-[var(--text)]",
            ),
      )}
    >
      <div className="flex items-center gap-2.5">
        <span className={cn(filled ? "text-[var(--invert-text)]" : "text-[var(--text)]")}>
          {mark}
        </span>
        <h2
          className={cn(
            "font-display text-[17px] font-bold tracking-tight",
            filled ? "text-[var(--invert-text)]" : "text-[var(--text)]",
          )}
        >
          {title}
        </h2>
      </div>
      {children}
    </section>
  );
}

function Body({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-1.5 max-w-2xl text-[13.5px] leading-relaxed opacity-80">{children}</p>
  );
}
