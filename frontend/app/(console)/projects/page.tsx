"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Search } from "lucide-react";

import ProjectCard from "@/components/console/ProjectCard";
import {
  Button,
  ButtonLink,
  EmptyState,
  Panel,
  SkeletonRows,
} from "@/components/ui/Primitives";
import { ApiError, api, isActive, type RunRecord } from "@/lib/api";
import { groupIntoProjects } from "@/lib/projects";

const POLL_INTERVAL_MS = 4000;

export default function ProjectsPage() {
  const [runs, setRuns] = useState<RunRecord[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [query, setQuery] = useState("");

  const refresh = useCallback(async () => {
    try {
      const { runs: fetched } = await api.listRuns(200);
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

  useEffect(() => {
    if (!runs?.some((run) => isActive(run.status))) return;
    const timer = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [runs, refresh]);

  const projects = useMemo(() => groupIntoProjects(runs ?? []), [runs]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return projects;
    return projects.filter(
      (project) =>
        project.name.toLowerCase().includes(needle) ||
        project.latest.requirement.toLowerCase().includes(needle),
    );
  }, [projects, query]);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-display text-[26px] font-bold tracking-tightest text-[var(--text)]">
            Projects
          </h1>
          <p className="mt-1 max-w-xl text-[13.5px] text-[var(--muted)]">
            Runs are grouped by the name you gave them, so repeated attempts at one idea stay
            together.
          </p>
        </div>
        <Button onClick={refresh} variant="ghost" className="shrink-0">
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </Button>
      </header>

      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--muted-soft)]" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Find a project"
          aria-label="Find a project"
          className="w-full rounded-lg border border-[var(--line)] bg-[var(--panel)] py-2 pl-9 pr-3 text-[13.5px] text-[var(--text)] placeholder:text-[var(--muted-soft)] focus:border-[var(--line-strong)] focus:outline-none"
        />
      </div>

      {error && (
        <Panel className="border-dashed p-4">
          <p className="text-[13.5px] font-medium text-[var(--text)]">
            {error.isOffline ? "The API is not reachable" : "The API returned an error"}
          </p>
          <p className="mt-0.5 font-mono text-[11.5px] text-[var(--muted)]">{error.message}</p>
        </Panel>
      )}

      {!runs ? (
        <SkeletonRows rows={4} />
      ) : visible.length === 0 ? (
        <EmptyState
          title={query ? "Nothing matches that" : "No projects yet"}
          body={
            query
              ? "Try a different name, or clear the search to see everything."
              : "Name a run when you start it and every later attempt at the same idea will collect here."
          }
          action={
            !query ? (
              <ButtonLink href="/new" variant="primary">
                Start a build
              </ButtonLink>
            ) : undefined
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {visible.map((project) => (
            <ProjectCard key={project.name} project={project} />
          ))}
        </div>
      )}
    </div>
  );
}
