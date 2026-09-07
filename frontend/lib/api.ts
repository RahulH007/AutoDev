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
  /** Model calls made by this run, settled when it comes to rest. */
  total_calls: number;
  /** Tokens reserved against the providers' per-minute windows. */
  total_tokens: number;
  /** Null until there is a real pricing source; never estimated here. */
  estimated_cost: number | null;
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

export interface UserFlow {
  name: string;
  actor: string;
  steps: string[];
  related_features?: string[];
}

export interface PrdModule {
  name: string;
  responsibility: string;
  exposes?: string[];
  depends_on?: string[];
}

export interface DataEntity {
  name: string;
  description: string;
  key_attributes?: string[];
  relationships?: string[];
}

export interface NonFunctionalRequirement {
  category: string;
  description: string;
  target_metric?: string | null;
}

export interface PrdEndpoint {
  method: string;
  path: string;
  description: string;
  auth_required?: boolean;
  related_module?: string | null;
}

/** Mirrors `schema/product_manager_schema.py:ManagerSchema`. */
export interface Prd {
  product_name?: string;
  product_summary?: string;
  problem_statement?: string;
  target_users?: string[];
  success_metrics?: string[];
  features?: Feature[];
  user_flows?: UserFlow[];
  modules?: PrdModule[];
  suggested_tech_stack?: string[];
  expected_scale?: string;
  data_entities?: DataEntity[];
  possible_apis?: PrdEndpoint[];
  functional_requirements?: string[];
  non_functional_requirements?: NonFunctionalRequirement[];
  constraints?: string[];
  assumptions?: string[];
  open_questions?: string[];
  out_of_scope?: string[];
  complexity_estimate?: string;
}

export interface ArchDataModel {
  name: string;
  description: string;
  fields: string[];
}

export interface ArchService {
  name: string;
  description: string;
  tech_stack: string[];
  dependencies?: string[];
  api_endpoints?: { method: string; path: string; description: string }[];
  data_models?: ArchDataModel[];
}

export interface ProjectStructure {
  service_name: string;
  folders: string[];
  key_files: string[];
}

export interface ExternalIntegration {
  name: string;
  purpose: string;
  integration_method: string;
}

export interface ImplementationTask {
  service: string;
  task: string;
  description: string;
}

/** Mirrors `schema/architect_schema.py:ArchitectSchema`. */
export interface Architecture {
  system_overview?: string;
  architecture_style?: string;
  services?: ArchService[];
  databases?: { name: string; type: string; purpose: string; entities?: string[] }[];
  external_integrations?: ExternalIntegration[];
  environment_variables?: { name: string; description: string; example?: string }[];
  project_structure?: ProjectStructure[];
  implementation_tasks?: ImplementationTask[];
  risks?: string[];
  development_notes?: string[];
  complexity_estimate?: string;
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

/**
 * Model spending, as `llm/accounting.py` records it.
 *
 * Two token figures, and the difference matters. `reserved_tokens` is what the
 * pipeline claimed against the provider's per-minute window — always known,
 * because the budget computes it before the call. `actual_tokens` is what the
 * provider said it billed, and is `null` whenever no call reported usage: most
 * of this pipeline is structured output, which arrives as a validated schema
 * object with the usage already stripped off. Rendering the estimate as though
 * it were measured usage is the one thing this must not do.
 */
export interface CostTotals {
  calls: number;
  estimated_tokens: number;
  reserved_tokens: number;
  actual_tokens: number | null;
  calls_with_usage: number;
  seconds: number;
  /** Null until a real pricing source exists. Never invented client-side. */
  estimated_cost: number | null;
  outcomes: Record<string, number>;
}

export interface CostReport extends CostTotals {
  by_stage?: Record<string, CostTotals>;
  /** Keyed `provider:model`, the same way the token budget is keyed. */
  by_model?: Record<string, CostTotals>;
  /**
   * Keyed `provider:account`, and absent unless a provider is pooled across
   * several accounts. An account is a non-secret identifier such as `groq-2`;
   * credentials never reach the API.
   */
  by_account?: Record<string, CostTotals>;
  /**
   * Keyed by model tier (`low`/`medium`/`high`), and absent unless difficulty
   * routing chose the model for at least one call.
   */
  by_tier?: Record<string, CostTotals>;
}

/**
 * The key `service_failures` uses for evidence that names no service.
 *
 * Safe beside real slugs because a slug can only contain lowercase letters,
 * digits and hyphens — see `core/paths.py:slugify`.
 */
export const UNATTRIBUTED = "_unattributed";

/**
 * Which service each verification failure implicates, keyed by slug.
 *
 * Derived at the gates by `agents/attribution.py` from the static, test and QA
 * reports the run already produced — nothing is inferred beyond what those say,
 * and a failure whose service could not be established is kept under
 * {@link UNATTRIBUTED} rather than assigned to one.
 */
export type ServiceFailures = Record<string, string[]>;

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
  /** Empty until something has been verified. */
  service_failures: ServiceFailures;
  /** Empty for a run that has not made a model call. */
  cost_report: CostReport | Record<string, never>;
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

/** The structured documents that can also be handed over as a PDF. */
export type ExportKind = "prd" | "architecture";

/** Which PDF each export produces, so a page can tell whether one exists yet. */
export const EXPORT_ARTIFACT: Record<ExportKind, string> = {
  prd: "product_manager.pdf",
  architecture: "architecture.pdf",
};

export interface ExportResponse {
  name: string;
  /** False when the file already existed, so no model call was spent. */
  generated: boolean;
  url: string;
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

  /**
   * Render a structured document as a PDF.
   *
   * An export, not a step: the panels on screen are built from the structured
   * JSON and never wait on this. Costs a model call the first time and only the
   * first time — an already exported document comes back with
   * `generated: false`.
   */
  exportPdf: (id: string, kind: ExportKind) =>
    request<ExportResponse>(`/api/runs/${id}/artifacts/${kind}/pdf`, { method: "POST" }),

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
