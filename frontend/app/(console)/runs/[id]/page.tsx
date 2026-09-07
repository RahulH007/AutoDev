"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";

import {
  ArchitectureDocument,
  PrdDocument,
  isEmptyArtifact,
} from "@/components/runs/ArtifactDocuments";
import ExportPdfButton from "@/components/runs/ExportPdfButton";
import FileExplorer from "@/components/runs/FileExplorer";
import LiveLog from "@/components/runs/LiveLog";
import PipelineStepper from "@/components/runs/PipelineStepper";
import RawJson from "@/components/runs/RawJson";
import ReviewPanel from "@/components/runs/ReviewPanel";
import RunStatusBanner from "@/components/runs/RunStatusBanner";
import ServiceManifest from "@/components/runs/ServiceManifest";
import UsagePanel from "@/components/runs/UsagePanel";
import VerificationPanel from "@/components/runs/VerificationPanel";
import StatusPill from "@/components/ui/StatusDot";
import {
  api,
  isActive,
  isAwaitingReview,
  type RunDetail,
  type RunEvent,
} from "@/lib/api";
import { cn, formatRelativeTime } from "@/lib/utils";

const POLL_INTERVAL_MS = 3000;

type Tab = "brief" | "architecture" | "services" | "verification" | "files" | "requirement";

/** Ordered as the pipeline produces them, so the tabs read as the run's history. */
const TABS: { id: Tab; label: string }[] = [
  { id: "brief", label: "Product brief" },
  { id: "architecture", label: "Architecture" },
  { id: "services", label: "Services" },
  { id: "verification", label: "Verification" },
  { id: "files", label: "Files" },
  { id: "requirement", label: "Requirement" },
];

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("requirement");
  const [busy, setBusy] = useState(false);
  const [latestEvent, setLatestEvent] = useState<RunEvent | null>(null);

  // The opening tab is chosen once, from what the run has actually produced.
  // Defaulting to Verification meant landing on an empty panel for most of a
  // run's life; re-choosing on every poll would move the tab under the user.
  const tabChosen = useRef(false);
  const reviewRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await api.getRun(id);
      setDetail(next);
      setError("");

      if (!tabChosen.current) {
        tabChosen.current = true;
        setTab(openingTab(next));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load this run.");
    }
  }, [id]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // While work is in flight the status changes without any event of its own, so
  // the summary is polled. The log itself arrives over SSE.
  useEffect(() => {
    if (!detail || !isActive(detail.run.status)) return;
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [detail, refresh]);

  if (error && !detail) {
    return (
      <>
        <div className="rounded-xl border border-[var(--line-strong)] bg-[var(--panel)] px-6 py-10 text-center">
          <AlertTriangle className="mx-auto mb-3 h-5 w-5 text-[var(--text)]" strokeWidth={1.9} />
          <p className="text-[13.5px] text-[var(--text)]">{error}</p>
          <Link
            href="/runs"
            className="mt-4 inline-block text-[12.5px] text-[var(--muted)] underline-offset-2 transition-colors hover:text-[var(--text)] hover:underline"
          >
            Back to all runs
          </Link>
        </div>
      </>
    );
  }

  if (!detail) {
    return (
      <>
        <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] px-6 py-14 text-center text-[13.5px] text-[var(--muted)]">
          <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
          Loading run…
        </div>
      </>
    );
  }

  const { run } = detail;

  const cancel = async () => {
    setBusy(true);
    try {
      await api.cancel(run.id);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await api.deleteRun(run.id);
      router.push("/dashboard");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="mb-7 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <Link
            href="/runs"
            className="mb-2.5 inline-flex items-center gap-1.5 text-[12px] text-[var(--muted)] transition-colors hover:text-[var(--text)]"
          >
            <ArrowLeft className="h-3 w-3" />
            All runs
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="truncate font-display text-[26px] font-bold tracking-tightest text-[var(--text)]">
              {run.name || "Untitled"}
            </h1>
            <StatusPill status={run.status} />
          </div>
          <p className="mt-2 font-mono text-[11px] text-[var(--muted-soft)]">
            {run.id} · updated {formatRelativeTime(run.updated_at)}
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={refresh}
            className="rounded-lg border border-[var(--line)] p-2 text-[var(--muted)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
            title="Refresh"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>

          {detail.has_zip && (
            <a
              href={api.zipUrl(run.id)}
              className="inline-flex items-center gap-2 rounded-lg bg-[var(--invert-bg)] px-4 py-2 text-[13.5px] font-semibold text-[var(--invert-text)] transition-opacity hover:opacity-90"
            >
              <Download className="w-4 h-4" />
              Download project
            </a>
          )}

          {!isTerminalStatus(run.status) && (
            <button
              onClick={cancel}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-lg border border-[var(--line-strong)] px-3 py-2 text-[13.5px] text-[var(--text)] transition-colors hover:bg-[var(--panel-2)] disabled:opacity-40"
            >
              <XCircle className="w-4 h-4" />
              Cancel
            </button>
          )}

          <button
            onClick={remove}
            disabled={busy}
            className="rounded-lg border border-[var(--line)] p-2 text-[var(--muted)] transition-colors hover:border-[var(--line-strong)] hover:text-[var(--text)] disabled:opacity-40"
            title="Delete run"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <div className="mb-7">
        <RunStatusBanner
          run={run}
          stages={detail.stages}
          latestEvent={latestEvent}
          hasZip={detail.has_zip}
          onSeeReview={() =>
            reviewRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })
          }
        />
      </div>

      <div className="mb-7">
        <PipelineStepper stages={detail.stages} />
      </div>

      {isAwaitingReview(run.status) && (
        <div className="mb-7" ref={reviewRef}>
          <ReviewPanel
            runId={run.id}
            status={run.status}
            prd={detail.prd}
            architecture={detail.architecture}
            onResumed={refresh}
          />
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_300px] xl:gap-7">
        <div className="min-w-0 space-y-7">
          <div>
            <div className="mb-4 flex flex-wrap items-center gap-1 border-b border-[var(--line)] pb-3">
              {TABS.map(({ id: tabId, label }) => (
                <button
                  key={tabId}
                  onClick={() => setTab(tabId)}
                  aria-current={tab === tabId ? "true" : undefined}
                  className={cn(
                    "rounded-md border px-3 py-1.5 text-xs transition-colors",
                    tab === tabId
                      ? "border-[var(--line-strong)] bg-[var(--panel-2)] font-medium text-[var(--text)]"
                      : "border-transparent text-[var(--muted)] hover:bg-[var(--panel-2)] hover:text-[var(--text)]",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>

            {tab === "brief" &&
              (isEmptyArtifact(detail.prd) ? (
                <ArtifactPending
                  title="No product brief yet"
                  body="The PM agent writes this first. It appears here as soon as that stage finishes, and stays available for the rest of the run."
                />
              ) : (
                <ArtifactSheet
                  raw={detail.prd}
                  action={
                    <ExportPdfButton runId={run.id} kind="prd" existing={detail.artifacts} />
                  }
                >
                  <PrdDocument prd={detail.prd} variant="full" />
                </ArtifactSheet>
              ))}

            {tab === "architecture" &&
              (isEmptyArtifact(detail.architecture) ? (
                <ArtifactPending
                  title="No architecture yet"
                  body="The architecture agent runs after the product brief is approved."
                />
              ) : (
                <ArtifactSheet
                  raw={detail.architecture}
                  action={
                    <ExportPdfButton
                      runId={run.id}
                      kind="architecture"
                      existing={detail.artifacts}
                    />
                  }
                >
                  <ArchitectureDocument architecture={detail.architecture} variant="full" />
                </ArtifactSheet>
              ))}

            {tab === "services" && <ServiceManifest manifest={detail.code_manifest} />}

            {tab === "verification" && (
              <VerificationPanel
                staticReport={detail.static_report}
                verification={detail.verification_report}
                qa={detail.qa_report}
                serviceFailures={detail.service_failures}
              />
            )}
            {tab === "files" && <FileExplorer runId={run.id} />}
            {tab === "requirement" && (
              <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
                <div className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
                  As you described it
                </div>
                <p className="mt-3 whitespace-pre-wrap text-[13.5px] leading-relaxed text-[var(--text)]">
                  {run.requirement}
                </p>
              </div>
            )}
          </div>

          <LiveLog runId={run.id} onRunSettled={refresh} onLatestEvent={setLatestEvent} />
        </div>

        <aside className="space-y-4">
          <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
            <div className="font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
              Summary
            </div>
            <dl className="mt-3.5 space-y-2.5">
              <Meta
                label="Quality"
                value={run.qa_score === null ? "—" : `${run.qa_score.toFixed(1)}/10`}
              />
              <Meta label="Attempts" value={`${run.retry_count}/3`} />
              <Meta label="Code" value={codeSummary(detail)} />
              <Meta label="Started" value={formatRelativeTime(run.created_at)} />
              {run.finished_at && (
                <Meta label="Finished" value={formatRelativeTime(run.finished_at)} />
              )}
            </dl>
          </div>

          <UsagePanel report={detail.cost_report} />

          {detail.artifacts.length > 0 && (
            <div className="overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)]">
              <div className="border-b border-[var(--line)] px-5 py-3 font-display text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
                Documents
              </div>
              <div className="divide-y divide-[var(--line)]">
                {detail.artifacts.map((name) => (
                  <a
                    key={name}
                    href={api.artifactUrl(run.id, name)}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-2.5 px-5 py-2.5 text-[12px] text-[var(--muted)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text)]"
                  >
                    <FileText className="w-3.5 h-3.5 shrink-0 text-[var(--muted-soft)]" />
                    <span className="truncate font-mono">{name}</span>
                  </a>
                ))}
              </div>
            </div>
          )}
        </aside>
      </div>
    </>
  );
}

/**
 * A structured document: the reading of it, then the thing itself.
 *
 * Three layers, in the order they are wanted. The rendered document is what the
 * page is for. `raw` puts the canonical artifact one click away for anyone who
 * needs to see exactly what the model produced. `action` — the PDF export — is
 * deliberately the quietest of the three, because a PDF is a copy of what is
 * already on the page rather than the way to read it.
 *
 * All three describe one object. Nothing here holds a second copy of it.
 */
function ArtifactSheet({
  children,
  action,
  raw,
}: {
  children: React.ReactNode;
  action?: React.ReactNode;
  raw?: object;
}) {
  return (
    <div className="rounded-xl border border-[var(--line)] bg-[var(--panel)] p-5">
      {action && <div className="mb-3 flex justify-end">{action}</div>}
      {children}
      {raw && <RawJson value={raw} />}
    </div>
  );
}

/**
 * Says where the run actually is rather than showing an empty document. Nothing
 * is invented: an artifact is absent until its stage produces it.
 */
function ArtifactPending({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-dashed border-[var(--line-strong)] px-5 py-8">
      <p className="font-display text-[14px] font-semibold text-[var(--text)]">{title}</p>
      <p className="mt-1 max-w-md text-[12.5px] leading-relaxed text-[var(--muted)]">{body}</p>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-[12px] text-[var(--muted)]">{label}</dt>
      <dd className="text-right font-mono text-[12px] text-[var(--text)]">{value}</dd>
    </div>
  );
}

/** Both counts in one row; the Services tab carries the detail behind them. */
function codeSummary(detail: RunDetail) {
  const services = Object.keys(detail.code_manifest ?? {}).length;
  if (services === 0) return "—";
  const files = Object.values(detail.code_manifest).reduce(
    (total, service) => total + (service.files?.length ?? 0),
    0,
  );
  return `${services} svc · ${files} files`;
}

/**
 * Open on the furthest thing the run has actually produced.
 *
 * Nothing is fabricated: each branch checks a payload the API already returned.
 * A run that has produced nothing yet opens on its own requirement, which is
 * the one panel that is never empty.
 */
function openingTab(detail: RunDetail): Tab {
  if (detail.run.status === "awaiting_architecture_review") return "architecture";
  if (detail.run.status === "awaiting_pm_review") return "brief";
  if (detail.verification_report?.ran) return "verification";
  if (Object.keys(detail.code_manifest ?? {}).length > 0) return "services";
  if (!isEmptyArtifact(detail.architecture)) return "architecture";
  if (!isEmptyArtifact(detail.prd)) return "brief";
  return "requirement";
}

function isTerminalStatus(status: RunDetail["run"]["status"]) {
  return status === "completed" || status === "failed" || status === "cancelled";
}
