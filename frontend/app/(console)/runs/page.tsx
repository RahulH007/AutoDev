"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Download,
  FolderOpen,
  Loader2,
  PauseCircle,
  Plus,
  RefreshCw,
  Search,
  ServerCrash,
  X,
} from "lucide-react";

import StatusBadge from "@/components/runs/StatusBadge";
import {
  ApiError,
  api,
  isActive,
  isAwaitingReview,
  type RunRecord,
  type RunStatus,
} from "@/lib/api";
import { cn, formatRelativeTime } from "@/lib/utils";

const POLL_INTERVAL_MS = 4000;

type Filter = "all" | "active" | "review" | "completed" | "failed";

const FILTERS: { id: Filter; label: string; match: (status: RunStatus) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "active", label: "Running", match: isActive },
  { id: "review", label: "Needs review", match: isAwaitingReview },
  { id: "completed", label: "Completed", match: (status) => status === "completed" },
  { id: "failed", label: "Failed", match: (status) => status === "failed" },
];

export default function RunsPage() {
  const [runs, setRuns] = useState<RunRecord[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [showCreate, setShowCreate] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const { runs: fetched } = await api.listRuns();
      setRuns(fetched);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, String(caught)));
      setRuns((current) => current ?? []);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Keep the list live while anything is still moving.
  useEffect(() => {
    if (!runs?.some((run) => isActive(run.status))) return;
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [runs, refresh]);

  const counts = useMemo(
    () => ({
      total: runs?.length ?? 0,
      completed: runs?.filter((run) => run.status === "completed").length ?? 0,
      active: runs?.filter((run) => isActive(run.status)).length ?? 0,
      review: runs?.filter((run) => isAwaitingReview(run.status)).length ?? 0,
    }),
    [runs],
  );

  const filtered = useMemo(() => {
    const predicate = FILTERS.find((entry) => entry.id === filter)!.match;
    const needle = query.trim().toLowerCase();

    return (runs ?? []).filter((run) => {
      if (!predicate(run.status)) return false;
      if (!needle) return true;
      return (
        run.name.toLowerCase().includes(needle) ||
        run.requirement.toLowerCase().includes(needle) ||
        run.id.includes(needle)
      );
    });
  }, [runs, filter, query]);

  return (
    <div className="max-w-7xl mx-auto px-6 py-10">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-8">
        <div>
          <div className="text-xs text-[var(--muted)] mb-1">Workspace / Console</div>
          <h1 className="text-3xl font-bold text-[var(--text)] tracking-tight">Runs</h1>
          <p className="text-[var(--muted)] text-sm mt-1.5">
            Every code-generation run, live from the pipeline.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            className="p-2.5 rounded-md border border-[var(--line)] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--panel-2)] transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-md bg-violet-600 hover:bg-violet-500 text-[var(--text)] text-sm font-medium transition-colors"
          >
            <Plus className="w-4 h-4" />
            New Run
          </button>
        </div>
      </div>

      {error?.isOffline && <OfflineNotice message={error.message} onRetry={refresh} />}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard label="Total Runs" value={counts.total} icon={FolderOpen} tone="violet" />
        <StatCard label="Completed" value={counts.completed} icon={CheckCircle2} tone="emerald" />
        <StatCard label="Running" value={counts.active} icon={Loader2} tone="blue" />
        <StatCard label="Needs Review" value={counts.review} icon={PauseCircle} tone="amber" />
      </div>

      <div className="glass-strong rounded-xl border border-[var(--line)] overflow-hidden">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 px-5 py-4 border-b border-[var(--line)]">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--muted)]" />
            <input
              type="text"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by name, requirement, or id..."
              className="w-full h-9 pl-9 pr-3 rounded-md bg-[var(--panel-2)] border border-[var(--line)] text-[var(--text)] text-sm placeholder-zinc-600 focus:outline-none focus:border-violet-500/40 transition-colors"
            />
          </div>

          <div className="flex items-center gap-2 text-xs flex-wrap">
            {FILTERS.map((entry) => (
              <button
                key={entry.id}
                onClick={() => setFilter(entry.id)}
                className={cn(
                  "px-2.5 py-1 rounded-md border transition-colors",
                  filter === entry.id
                    ? "bg-violet-600/15 border-violet-500/30 text-violet-300"
                    : "border-[var(--line)] text-[var(--muted)] hover:border-[var(--line-strong)] hover:text-[var(--text)]",
                )}
              >
                {entry.label}
              </button>
            ))}
          </div>
        </div>

        <div className="hidden md:grid grid-cols-12 gap-4 px-5 py-3 text-[11px] uppercase tracking-wider text-[var(--muted)] border-b border-[var(--line)]">
          <div className="col-span-5">Run</div>
          <div className="col-span-2">Stage</div>
          <div className="col-span-1">Attempts</div>
          <div className="col-span-1">QA</div>
          <div className="col-span-3 text-right">Updated</div>
        </div>

        <div className="divide-y divide-white/[0.04]">
          {runs === null ? (
            <Placeholder>
              <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
              Loading runs…
            </Placeholder>
          ) : filtered.length === 0 ? (
            <Placeholder>
              {runs.length === 0 ? (
                <>
                  Nothing here yet. Start your first run with{" "}
                  <button
                    onClick={() => setShowCreate(true)}
                    className="text-violet-400 hover:text-violet-300"
                  >
                    New Run
                  </button>
                  .
                </>
              ) : (
                "No runs match your filters."
              )}
            </Placeholder>
          ) : (
            filtered.map((run) => <RunRow key={run.id} run={run} />)
          )}
        </div>

        <div className="flex items-center justify-between px-5 py-3 border-t border-[var(--line)] text-xs text-[var(--muted)]">
          <span>
            Showing {filtered.length} of {runs?.length ?? 0}
          </span>
        </div>
      </div>

      {showCreate && <CreateRunModal onClose={() => setShowCreate(false)} />}
    </div>
  );
}

function OfflineNotice({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="mb-6 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-5 py-4">
      <div className="flex items-start gap-3">
        <ServerCrash className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
        <div className="min-w-0">
          <p className="text-amber-300 text-sm font-medium">The API is not responding</p>
          <p className="text-[var(--muted)] text-xs mt-1 leading-relaxed">{message}</p>
          <button
            onClick={onRetry}
            className="mt-2 text-xs text-violet-400 hover:text-violet-300 transition-colors"
          >
            Try again
          </button>
        </div>
      </div>
    </div>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return <div className="px-5 py-16 text-center text-[var(--muted)] text-sm">{children}</div>;
}

function StatCard({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: number;
  icon: typeof FolderOpen;
  tone: "violet" | "emerald" | "blue" | "amber";
}) {
  const tones: Record<string, string> = {
    violet: "text-violet-400 bg-violet-500/10 border-violet-500/20",
    emerald: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
    blue: "text-blue-400 bg-blue-500/10 border-blue-500/20",
    amber: "text-amber-400 bg-amber-500/10 border-amber-500/20",
  };

  return (
    <div className="glass rounded-xl border border-[var(--line)] p-5">
      <div className="flex items-start justify-between mb-3">
        <span className="text-[var(--muted)] text-xs font-medium uppercase tracking-wider">{label}</span>
        <div className={cn("w-8 h-8 rounded-md flex items-center justify-center border", tones[tone])}>
          <Icon className="w-4 h-4" />
        </div>
      </div>
      <div className="text-3xl font-semibold text-[var(--text)]">{value}</div>
    </div>
  );
}

function RunRow({ run }: { run: RunRecord }) {
  return (
    <Link
      href={`/runs/${run.id}`}
      className="grid grid-cols-1 md:grid-cols-12 gap-2 md:gap-4 px-5 py-4 hover:bg-[var(--panel-2)] transition-colors items-center"
    >
      <div className="md:col-span-5 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[var(--text)] font-medium text-sm truncate">{run.name}</span>
          <StatusBadge status={run.status} />
        </div>
        <p className="text-[var(--muted)] text-xs truncate">{run.requirement}</p>
        <div className="text-[11px] text-[var(--muted-soft)] mt-0.5 font-mono">{run.id.slice(0, 12)}</div>
      </div>

      <div className="md:col-span-2 text-sm text-[var(--muted)] truncate">
        {stageLabel(run.current_stage)}
      </div>

      <div className="md:col-span-1 text-sm text-[var(--muted)]">{run.retry_count}/3</div>

      <div className="md:col-span-1 text-sm">
        {run.qa_score === null ? (
          <span className="text-[var(--muted-soft)]">—</span>
        ) : (
          <span
            className={cn(
              "font-medium",
              run.qa_score >= 8
                ? "text-emerald-400"
                : run.qa_score >= 7
                  ? "text-amber-400"
                  : "text-red-400",
            )}
          >
            {run.qa_score.toFixed(1)}
          </span>
        )}
      </div>

      <div className="md:col-span-3 flex items-center justify-end gap-2 text-xs text-[var(--muted)]">
        <Clock className="w-3 h-3" />
        <span>{formatRelativeTime(run.updated_at)}</span>
        {run.zip_path && (
          <span
            className="ml-2 p-1.5 rounded-md text-[var(--muted)]"
            title="A downloadable archive is ready"
          >
            <Download className="w-3.5 h-3.5" />
          </span>
        )}
        <ChevronRight className="w-3.5 h-3.5" />
      </div>
    </Link>
  );
}

const STAGE_LABELS: Record<string, string> = {
  pm_agent: "Product Manager",
  architecture_agent: "Architect",
  developer_agent: "Developer",
  static_gate: "Static Gate",
  qa_agent: "QA Engineer",
  test_runner: "Test Runner",
};

function stageLabel(stage: string) {
  return STAGE_LABELS[stage] ?? "—";
}

function CreateRunModal({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [requirement, setRequirement] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const examples = [
    "Build a SaaS expense tracker with login, expense CRUD, categories, and monthly PDF reports",
    "Create a multi-user blog platform with markdown editor, tags, comments, and author profiles",
    "Build a real-time chat app with rooms, direct messages, file uploads, and read receipts",
    "Create a project management tool with kanban boards, task assignments, and deadline tracking",
  ];

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!requirement.trim()) return;

    setSubmitting(true);
    setError("");
    try {
      const run = await api.createRun(requirement.trim(), name.trim() || undefined);
      router.push(`/runs/${run.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start the run.");
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="relative w-full max-w-2xl glass-strong rounded-xl border border-[var(--line-strong)] max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--line)] sticky top-0 glass-strong z-10">
          <div>
            <h2 className="text-[var(--text)] font-semibold">New Run</h2>
            <p className="text-xs text-[var(--muted)] mt-0.5">
              Describe what you want built. The agent pipeline takes it from there.
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--panel-2)] transition-colors"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={submit} className="p-6 space-y-5">
          <div>
            <label className="block text-xs font-medium text-[var(--muted)] mb-2">
              Name
              <span className="ml-1.5 text-[var(--muted-soft)] font-normal">
                — optional, the PM agent will name it otherwise
              </span>
            </label>
            <input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g., Expense Tracker"
              className="w-full h-10 px-3 rounded-md bg-[var(--panel-2)] border border-[var(--line)] text-[var(--text)] text-sm placeholder-zinc-600 focus:outline-none focus:border-violet-500/40 transition-colors"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-[var(--muted)] mb-2">
              Requirements
              <span className="ml-1.5 text-[var(--muted-soft)] font-normal">— what should the system do?</span>
            </label>
            <textarea
              value={requirement}
              onChange={(event) => setRequirement(event.target.value)}
              placeholder="Describe features, user roles, and any technical preferences..."
              rows={5}
              required
              className="w-full px-3 py-2 rounded-md bg-[var(--panel-2)] border border-[var(--line)] text-[var(--text)] text-sm placeholder-zinc-600 focus:outline-none focus:border-violet-500/40 transition-colors resize-none leading-relaxed"
            />
            <div className="flex items-center justify-between mt-1.5">
              <p className="text-[11px] text-[var(--muted-soft)]">
                Tip: include user roles, key features, and any preferred tech stack.
              </p>
              <span className="text-[11px] text-[var(--muted-soft)]">{requirement.length} chars</span>
            </div>
          </div>

          <div>
            <p className="text-xs text-[var(--muted)] mb-2 font-medium uppercase tracking-wider">
              Example prompts
            </p>
            <div className="space-y-2">
              {examples.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setRequirement(example)}
                  className="w-full text-left px-3 py-2 rounded-md border border-[var(--line)] bg-[var(--panel-2)] hover:bg-[var(--panel-2)] hover:border-white/10 transition-colors text-xs text-[var(--muted)] hover:text-[var(--text)]"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>

          <div className="rounded-md border border-[var(--line)] bg-[var(--panel-2)] p-4">
            <p className="text-[11px] text-[var(--muted)] mb-2 font-medium uppercase tracking-wider">
              Pipeline
            </p>
            <div className="flex items-center gap-2 text-xs text-[var(--muted)] flex-wrap">
              {["PM", "Architect", "Developer", "Static Gate", "QA", "Test Runner"].map(
                (stage, index, all) => (
                  <span key={stage} className="flex items-center gap-2">
                    <span className="px-2 py-1 rounded border border-[var(--line)] bg-[var(--panel-2)]">
                      {stage}
                    </span>
                    {index < all.length - 1 && <span className="text-[var(--muted-soft)]">/</span>}
                  </span>
                ),
              )}
            </div>
            <p className="text-[11px] text-[var(--muted-soft)] mt-2">
              The run pauses after the PM and architecture stages for your approval. Generated code
              is compiled and its tests are executed before the run is called done.
            </p>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-red-500/25 bg-red-500/[0.06] px-3 py-2.5">
              <AlertTriangle className="w-3.5 h-3.5 text-red-400 mt-0.5 shrink-0" />
              <p className="text-xs text-red-300 leading-relaxed">{error}</p>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-md text-sm text-[var(--text)] hover:text-[var(--text)] hover:bg-[var(--panel-2)] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !requirement.trim()}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium bg-violet-600 hover:bg-violet-500 text-[var(--text)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              {submitting ? "Starting..." : "Start Pipeline"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
