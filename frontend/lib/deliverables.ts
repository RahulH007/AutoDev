/**
 * What separates /projects from /runs.
 *
 * There is no project entity. A run is the only thing the API has, `run.id` is
 * the only identity, and `run.name` is a display label that the backend
 * rewrites to the PRD's `product_name` once the PM stage lands. Grouping by
 * that label merged unrelated runs — two ideas left unnamed, or two runs whose
 * PM agent happened to pick "Task Manager", became one card.
 *
 * So every run stands alone, keyed by id. The two pages differ by *which* runs
 * they show and what they are for, using only fields `/api/runs` returns:
 *
 *   /runs      every run — the operational history, including failures and
 *              work still in flight.
 *   /projects  runs that finished and produced something — the deliverables.
 *
 * `status === "completed"` is set in `service.py:_settle` only when the graph
 * came to rest with no error, and a zip is packaged in the same branch. So a
 * completed run is exactly "this produced a project you can open and download",
 * which is a real distinction rather than an invented one.
 */

import type { RunRecord } from "@/lib/api";

/** A finished run: the pipeline reached the end without an error. */
export function isDelivered(run: RunRecord): boolean {
  return run.status === "completed";
}

/** Whether the packaged archive exists, so the download can be offered honestly. */
export function hasDownload(run: RunRecord): boolean {
  return Boolean(run.zip_path);
}

/** Delivered runs, newest first. Each one is its own item — never merged. */
export function deliveredRuns(runs: RunRecord[]): RunRecord[] {
  return runs
    .filter(isDelivered)
    .sort((a, b) => timeOf(b.finished_at || b.created_at) - timeOf(a.finished_at || a.created_at));
}

function timeOf(value: string): number {
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}
