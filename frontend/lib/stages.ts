/**
 * The pipeline, as the graph actually defines it.
 *
 * Mirrors `state/state.py:Stage` and the wiring in `graph/build_graph.py`, the
 * same way `lib/api.ts` mirrors `server/models.py`. When a stage is added to the
 * graph it has to be added here too, or a run will walk off the end of the track.
 *
 * `/api/runs` returns only `current_stage`, so the track is derived from this
 * order. `/api/runs/{id}` returns real `StageProgress[]`, which is authoritative
 * wherever it is available.
 */

export interface Stage {
  id: string;
  /** Full name, for labels and tooltips. */
  label: string;
  /** Four characters, for the track. Longer and the track stops being scannable. */
  short: string;
}

export const STAGES: Stage[] = [
  { id: "pm_agent", label: "Product brief", short: "PRD" },
  { id: "architecture_agent", label: "Architecture", short: "ARCH" },
  { id: "developer_agent", label: "Source code", short: "CODE" },
  { id: "static_gate", label: "Compile check", short: "GATE" },
  { id: "qa_agent", label: "Review & tests", short: "QA" },
  { id: "test_runner", label: "Test run", short: "TEST" },
];

export function stageIndex(stageId: string | null | undefined): number {
  if (!stageId) return -1;
  return STAGES.findIndex((stage) => stage.id === stageId);
}

export function stageLabel(stageId: string | null | undefined): string {
  const stage = STAGES.find((candidate) => candidate.id === stageId);
  if (stage) return stage.label;
  // An unknown stage is shown as-is rather than hidden: it means this file has
  // drifted from the graph, and saying so is more useful than a blank.
  return stageId ? stageId.replace(/_/g, " ") : "Not started";
}
