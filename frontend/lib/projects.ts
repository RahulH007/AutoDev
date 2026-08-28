/**
 * Projects, derived from runs.
 *
 * The API has no project concept — there is no endpoint for one and none is
 * invented here. What it does have is `RunRecord.name`, which the CLI and the
 * console both set when a run is started. A project is therefore defined as
 * *every run sharing a name*, computed on the client from `/api/runs`.
 *
 * That is a real relationship rather than a fabricated one, but it is a
 * derivation, so it is stated here rather than implied by the UI.
 */

import { isActive, isAwaitingReview, type RunRecord } from "@/lib/api";

export interface Project {
  /** The shared `RunRecord.name`. Also the identity — there is no project id. */
  name: string;
  runs: RunRecord[];
  /** Most recent run, which decides how the project reads at a glance. */
  latest: RunRecord;
  runCount: number;
  /** Highest QA score any run in this project reached, if any run scored. */
  bestScore: number | null;
  hasActivity: boolean;
}

const UNNAMED = "Untitled";

export function groupIntoProjects(runs: RunRecord[]): Project[] {
  const byName = new Map<string, RunRecord[]>();

  for (const run of runs) {
    const name = run.name?.trim() || UNNAMED;
    const bucket = byName.get(name);
    if (bucket) bucket.push(run);
    else byName.set(name, [run]);
  }

  const projects: Project[] = [];

  for (const [name, group] of Array.from(byName.entries())) {
    const sorted = [...group].sort(
      (a, b) => timeOf(b.created_at) - timeOf(a.created_at),
    );
    const scores = sorted
      .map((run) => run.qa_score)
      .filter((score): score is number => typeof score === "number");

    projects.push({
      name,
      runs: sorted,
      latest: sorted[0],
      runCount: sorted.length,
      bestScore: scores.length > 0 ? Math.max(...scores) : null,
      hasActivity: sorted.some(
        (run) => isActive(run.status) || isAwaitingReview(run.status),
      ),
    });
  }

  return projects.sort(
    (a, b) => timeOf(b.latest.created_at) - timeOf(a.latest.created_at),
  );
}

function timeOf(value: string): number {
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}
