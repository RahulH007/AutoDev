"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, RefreshCw } from "lucide-react";

import ProjectCard from "@/components/console/ProjectCard";
import RunRow from "@/components/console/RunRow";
import StageTrack from "@/components/ui/StageTrack";
import {
  Button,
  EmptyState,
  Panel,
  SectionHeader,
  SkeletonRows,
} from "@/components/ui/Primitives";
import { ApiError, api, isActive, type RunRecord } from "@/lib/api";
import { groupIntoProjects } from "@/lib/projects";

const POLL_INTERVAL_MS = 4000;
const RECENT_PROJECTS = 4;
const RECENT_RUNS = 5;

export default function DashboardPage() {
  const router = useRouter();
  const [runs, setRuns] = useState<RunRecord[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [idea, setIdea] = useState("");
  const [starting, setStarting] = useState(false);

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

  // Keep the overview live only while something is actually moving.
  useEffect(() => {
    if (!runs?.some((run) => isActive(run.status))) return;
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [runs, refresh]);

  const projects = useMemo(() => groupIntoProjects(runs ?? []), [runs]);

  const start = async () => {
    const requirement = idea.trim();
    if (requirement.length === 0 || starting) return;

    setStarting(true);
    try {
      const run = await api.createRun(requirement);
      router.push(`/runs/${run.id}`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, String(caught)));
      setStarting(false);
    }
  };

  return (
    <div className="space-y-10">
      <BuildCta
        value={idea}
        onChange={setIdea}
        onStart={start}
        starting={starting}
        disabled={Boolean(error?.isOffline)}
      />

      {error && <ApiNotice error={error} onRetry={refresh} />}

      <section>
        <SectionHeader
          title="Recent projects"
          count={runs ? projects.length : undefined}
          action={
            projects.length > RECENT_PROJECTS ? (
              <Link
                href="/projects"
                className="inline-flex items-center gap-1 text-[12.5px] text-[var(--muted)] transition-colors hover:text-[var(--text)]"
              >
                All projects
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            ) : undefined
          }
        />

        {!runs ? (
          <SkeletonRows rows={2} />
        ) : projects.length === 0 ? (
          <EmptyState
            title="No projects yet"
            body="Runs started with the same name are grouped into a project, so you can see how an idea changed across attempts."
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {projects.slice(0, RECENT_PROJECTS).map((project) => (
              <ProjectCard key={project.name} project={project} />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionHeader
          title="Recent runs"
          count={runs?.length}
          action={
            (runs?.length ?? 0) > RECENT_RUNS ? (
              <Link
                href="/runs"
                className="inline-flex items-center gap-1 text-[12.5px] text-[var(--muted)] transition-colors hover:text-[var(--text)]"
              >
                All runs
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            ) : undefined
          }
        />

        {!runs ? (
          <SkeletonRows rows={3} />
        ) : runs.length === 0 ? (
          <EmptyState
            title="Nothing has run yet"
            body="Describe what you want built in the box above. The pipeline pauses twice for your review before it writes any code."
          />
        ) : (
          <div className="space-y-2">
            {runs.slice(0, RECENT_RUNS).map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

/* ── The one loud thing on the page ──────────────────────────── */

function BuildCta({
  value,
  onChange,
  onStart,
  starting,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  onStart: () => void;
  starting: boolean;
  disabled: boolean;
}) {
  const ready = value.trim().length > 0;

  return (
    <section className="animate-fade-up rounded-2xl bg-[var(--invert-bg)] p-6 text-[var(--invert-text)] shadow-[var(--shadow-card)] sm:p-8">
      <div className="max-w-2xl">
        <p className="font-mono text-[10.5px] uppercase tracking-[0.18em] opacity-55">
          One paragraph in, a tested codebase out
        </p>
        <h1 className="mt-3 font-display text-[30px] font-bold leading-[1.08] tracking-tightest sm:text-[38px]">
          Build your next POC
        </h1>
        <p className="mt-2.5 text-[14.5px] leading-relaxed opacity-70">
          Describe the product. Six agents write the brief, design the architecture, generate the
          code and run the tests — pausing twice so you can steer.
        </p>
      </div>

      <div className="mt-6">
        <label htmlFor="idea" className="sr-only">
          What do you want to build?
        </label>
        <textarea
          id="idea"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") onStart();
          }}
          rows={3}
          placeholder="An AI customer-support platform where users upload documents and ask questions about them."
          className="w-full resize-none rounded-xl border border-[color-mix(in_srgb,var(--invert-text)_18%,transparent)] bg-[color-mix(in_srgb,var(--invert-text)_7%,transparent)] px-4 py-3.5 text-[14.5px] leading-relaxed text-[var(--invert-text)] placeholder:text-[color-mix(in_srgb,var(--invert-text)_40%,transparent)] focus:border-[color-mix(in_srgb,var(--invert-text)_38%,transparent)] focus:outline-none"
        />

        <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-[220px] flex-1">
            <StageTrack idle size="sm" withLabels tone="invert" className="opacity-70" />
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={onStart}
              disabled={!ready || starting || disabled}
              className="inline-flex items-center gap-2 rounded-lg bg-[var(--invert-text)] px-5 py-2.5 text-[13.5px] font-semibold text-[var(--invert-bg)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {starting ? "Starting…" : "Start build"}
              {!starting && <ArrowRight className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function ApiNotice({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  return (
    <Panel className="flex flex-wrap items-center justify-between gap-4 border-dashed p-4">
      <div>
        <p className="text-[13.5px] font-medium text-[var(--text)]">
          {error.isOffline ? "The API is not reachable" : "The API returned an error"}
        </p>
        <p className="mt-0.5 font-mono text-[11.5px] text-[var(--muted)]">{error.message}</p>
      </div>
      <Button onClick={onRetry} className="shrink-0">
        <RefreshCw className="h-3.5 w-3.5" />
        Try again
      </Button>
    </Panel>
  );
}
