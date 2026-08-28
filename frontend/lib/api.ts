/**
 * Typed client for the AgentForge API.
 *
 * The types here mirror the Pydantic models in `server/models.py`. When one side
 * changes, the other has to change with it.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";

// ── Types ────────────────────────────────────────────────────────

export type RunStatus =
  | "queued"
  | "running"
  | "awaiting_pm_review"
  | "awaiting_architecture_review"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentStatus = "PENDING" | "IN_PROGRESS" | "COMPLETED" | "FAILED";

export type EventLevel = "debug" | "info" | "warning" | "error" | "stage";

export interface RunRecord {
  id: string;
  name: string;
  requirement: string;
  status: RunStatus;
  current_stage: string;
  retry_count: number;
  qa_score: number | null;
  workspace: string;
  zip_path: string;
  error: string;
  created_at: string;
  updated_at: string;
  finished_at: string;
}

export interface StageProgress {
  id: string;
  label: string;
  status: AgentStatus;
}

export interface RunEvent {
  id: number;
  run_id: string;
  ts: string;
  level: EventLevel;
  stage: string;
  message: string;
}

export interface Feature {
  name: string;
  description: string;
  priority: string;
  is_mvp: boolean;
  acceptance_criteria: string[];
}

export interface Prd {
  product_name?: string;
  product_summary?: string;
  problem_statement?: string;
  target_users?: string[];
  success_metrics?: string[];
  features?: Feature[];
  functional_requirements?: string[];
  constraints?: string[];
  open_questions?: string[];
  out_of_scope?: string[];
  complexity_estimate?: string;
}

export interface ArchService {
  name: string;
  description: string;
  tech_stack: string[];
  dependencies?: string[];
  api_endpoints?: { method: string; path: string; description: string }[];
}

export interface Architecture {
  system_overview?: string;
  architecture_style?: string;
  services?: ArchService[];
  databases?: { name: string; type: string; purpose: string }[];
  environment_variables?: { name: string; description: string; example?: string }[];
  risks?: string[];
  development_notes?: string[];
}

export interface QaBug {
  file_path: string;
  line_number: string;
  severity: string;
  description: string;
  suggested_fix: string;
}

export interface QaServiceReport {
  service_name: string;
  bugs: QaBug[];
  code_quality_score: number;
}

export interface QaReport {
  overall_assessment?: string;
  service_reports?: QaServiceReport[];
  critical_issues?: number;
  total_bugs_found?: number;
  total_tests_written?: number;
  recommendations?: string[];
  passed?: boolean;
}

export interface StaticReport {
  ran?: boolean;
  passed?: boolean;
  failures?: string[];
}

export interface TestFailure {
  test: string;
  file: string;
  message: string;
}

export interface ServiceTestResult {
  service: string;
  ran: boolean;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  failures: TestFailure[];
  error: string;
  output: string;
}

export interface VerificationReport {
  ran?: boolean;
  passed?: boolean;
  services?: ServiceTestResult[];
  summary?: string;
}

export interface ManifestEntry {
  file_path: string;
  description: string;
  language: string;
}

export type CodeManifest = Record<string, { display_name: string; files: ManifestEntry[] }>;

export interface RunDetail {
  run: RunRecord;
  stages: StageProgress[];
  is_running: boolean;
  prd: Prd;
  architecture: Architecture;
  code_manifest: CodeManifest;
  qa_report: QaReport;
  static_report: StaticReport;
  verification_report: VerificationReport;
  artifacts: string[];
  has_zip: boolean;
}

export interface RunListResponse {
  runs: RunRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface FileNode {
  path: string;
  size: number;
  is_generated_test: boolean;
}

export interface FileContent {
  path: string;
  language: string;
  content: string;
}

// ── Errors ───────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** True when the API could not be reached at all. */
  get isOffline() {
    return this.status === 0;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      `Cannot reach the API at ${API_URL}. Start it with \`python scripts/dev.py\`.`,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const body = text ? safeJson(text) : null;

  if (!response.ok) {
    throw new ApiError(response.status, detailOf(body) ?? `Request failed (${response.status}).`);
  }

  return body as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function detailOf(body: unknown): string | null {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      return first.msg ?? JSON.stringify(detail[0]);
    }
  }
  return null;
}

// ── Runs ─────────────────────────────────────────────────────────

export const api = {
  health: () => request<{ status: string; runs: number }>("/api/health"),

  listRuns: (limit = 50, offset = 0) =>
    request<RunListResponse>(`/api/runs?limit=${limit}&offset=${offset}`),

  getRun: (id: string) => request<RunDetail>(`/api/runs/${id}`),

  createRun: (requirement: string, name?: string) =>
    request<RunRecord>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ requirement, name: name || null, auto_start: true }),
    }),

  approve: (id: string) => request<RunRecord>(`/api/runs/${id}/approve`, { method: "POST" }),

  sendFeedback: (id: string, feedback: string) =>
    request<RunRecord>(`/api/runs/${id}/feedback`, {
      method: "POST",
      body: JSON.stringify({ feedback }),
    }),

  cancel: (id: string) => request<RunRecord>(`/api/runs/${id}/cancel`, { method: "POST" }),

  deleteRun: (id: string) => request<void>(`/api/runs/${id}`, { method: "DELETE" }),

  log: (id: string, afterId = 0) =>
    request<RunEvent[]>(`/api/runs/${id}/log?after_id=${afterId}`),

  listFiles: (id: string) => request<{ files: FileNode[] }>(`/api/runs/${id}/files`),

  readFile: (id: string, path: string) =>
    request<FileContent>(`/api/runs/${id}/files/${encodeURI(path)}`),

  artifactUrl: (id: string, name: string) => `${API_URL}/api/runs/${id}/artifacts/${name}`,

  zipUrl: (id: string) => `${API_URL}/api/runs/${id}/zip`,
};

// ── Live events ──────────────────────────────────────────────────

export interface StreamHandlers {
  onEvent: (event: RunEvent) => void;
  onEnd?: (run: RunRecord) => void;
  onError?: () => void;
}

/**
 * Subscribe to a run's live log.
 *
 * `afterId` lets a reconnecting client replay only what it missed, so the log
 * stays complete across a dropped connection. Returns a cleanup function.
 */
export function streamEvents(
  runId: string,
  { onEvent, onEnd, onError }: StreamHandlers,
  afterId = 0,
): () => void {
  const source = new EventSource(`${API_URL}/api/runs/${runId}/events?after_id=${afterId}`);

  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as RunEvent);
    } catch {
      /* a malformed frame is not worth tearing the stream down for */
    }
  };

  source.addEventListener("end", (message) => {
    try {
      onEnd?.(JSON.parse((message as MessageEvent).data) as RunRecord);
    } catch {
      onEnd?.(undefined as unknown as RunRecord);
    }
    source.close();
  });

  source.onerror = () => {
    source.close();
    onError?.();
  };

  return () => source.close();
}

// ── Display helpers ──────────────────────────────────────────────

export const STATUS_LABELS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_pm_review: "Awaiting PRD review",
  awaiting_architecture_review: "Awaiting architecture review",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function isTerminal(status: RunStatus) {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export function isAwaitingReview(status: RunStatus) {
  return status === "awaiting_pm_review" || status === "awaiting_architecture_review";
}

export function isActive(status: RunStatus) {
  return status === "queued" || status === "running";
}
