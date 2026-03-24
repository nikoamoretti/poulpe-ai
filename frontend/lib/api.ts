import type {
  ApiMessage,
  EventEnvelope,
  HealthResponse,
  Portfolio,
  Project,
  ProjectCheckpoint,
  ProjectCheckpointAction,
  Review,
  RuntimeStatus,
  Session,
  SessionRole,
  Task,
} from "@/lib/types";

const fallbackApiBaseUrl =
  typeof window === "undefined"
    ? "http://127.0.0.1:8001"
    : `${window.location.protocol}//${window.location.hostname}:8001`;

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  process.env.INTERNAL_API_BASE_URL ??
  fallbackApiBaseUrl;

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type RequestOptions = RequestInit & {
  query?: Record<string, string | number | undefined | null>;
};

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(path, apiBaseUrl);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") {
        continue;
      }
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { query, headers, ...init } = options;
  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      ...init,
      cache: init.method && init.method !== "GET" ? "no-store" : "no-store",
      headers: {
        "Content-Type": "application/json",
        ...headers,
      },
    });
  } catch {
    throw new ApiError(
      `Unable to reach the API at ${apiBaseUrl}. Check that the backend is running and that CORS allows this frontend origin.`,
      0,
    );
  }

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) {
        message = payload.detail;
      }
    } catch {}
    throw new ApiError(message, response.status);
  }

  return (await response.json()) as T;
}

export async function getApiHealth(): Promise<HealthResponse> {
  try {
    return await requestJson<HealthResponse>("/api/v1/health");
  } catch {
    return {
      status: "unreachable",
      service: "Orchestrator API",
      version: "unknown",
      checks: {},
    };
  }
}

export function getProjectEventsWebSocketUrl(projectId: string): string {
  const explicitBase = process.env.NEXT_PUBLIC_WS_BASE_URL;
  if (explicitBase) {
    return new URL(`/ws/projects/${projectId}/events`, explicitBase).toString();
  }

  const url = new URL(apiBaseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `/ws/projects/${projectId}/events`;
  url.search = "";
  return url.toString();
}

export async function listPortfolios(): Promise<Portfolio[]> {
  return requestJson<Portfolio[]>("/api/v1/portfolios");
}

export async function createPortfolio(input: {
  name: string;
  goal: string;
  metadata?: Record<string, unknown>;
}): Promise<Portfolio> {
  return requestJson<Portfolio>("/api/v1/portfolios", {
    method: "POST",
    body: JSON.stringify({
      name: input.name,
      goal: input.goal,
      metadata: input.metadata ?? {},
    }),
  });
}

export async function startPortfolioManager(input: {
  portfolioId: string;
  runtimePreference?: string | null;
  allowSimulationFallback?: boolean | null;
  simulationMode?: boolean | null;
  model?: string | null;
  initialMessage?: string | null;
  metadata?: Record<string, unknown>;
}): Promise<Session> {
  return requestJson<Session>(`/api/v1/portfolios/${input.portfolioId}/manager/start`, {
    method: "POST",
    body: JSON.stringify({
      runtime_preference: input.runtimePreference ?? null,
      allow_simulation_fallback: input.allowSimulationFallback ?? null,
      simulation_mode: input.simulationMode ?? null,
      model: input.model ?? null,
      initial_message: input.initialMessage ?? null,
      metadata: input.metadata ?? {},
    }),
  });
}

export async function listPortfolioInbox(
  portfolioId: string,
  status: "open" | "resolved" | "dismissed" | "all" = "open",
): Promise<ProjectCheckpoint[]> {
  return requestJson<ProjectCheckpoint[]>(`/api/v1/portfolios/${portfolioId}/inbox`, {
    query: { status: status === "all" ? null : status },
  });
}

export async function respondToPortfolioCheckpoint(input: {
  portfolioId: string;
  checkpointId: string;
  action: ProjectCheckpointAction;
  message?: string | null;
  details?: Record<string, unknown>;
}): Promise<ProjectCheckpoint> {
  return requestJson<ProjectCheckpoint>(
    `/api/v1/portfolios/${input.portfolioId}/inbox/${input.checkpointId}/respond`,
    {
      method: "POST",
      body: JSON.stringify({
        action: input.action,
        message: input.message ?? null,
        details: input.details ?? {},
      }),
    },
  );
}

export async function listProjects(portfolioId?: string | null): Promise<Project[]> {
  return requestJson<Project[]>("/api/v1/projects", {
    query: { portfolio_id: portfolioId ?? null },
  });
}

export async function createProject(input: {
  portfolioId?: string | null;
  name: string;
  repoPath?: string | null;
  createRepo?: boolean;
  defaultBranch?: string | null;
  objective: string;
  metadata?: Record<string, unknown>;
}): Promise<Project> {
  return requestJson<Project>("/api/v1/projects", {
    method: "POST",
    body: JSON.stringify({
      portfolio_id: input.portfolioId ?? null,
      name: input.name,
      repo_path: input.repoPath ?? null,
      create_repo: input.createRepo ?? false,
      default_branch: input.defaultBranch ?? null,
      objective: input.objective,
      metadata: input.metadata ?? {},
    }),
  });
}

export async function getRuntimeStatus(role: SessionRole = "worker"): Promise<RuntimeStatus> {
  return requestJson<RuntimeStatus>("/api/v1/runtime", {
    query: { role },
  });
}

export async function listScopeOptions(projectId: string): Promise<string[]> {
  return requestJson<string[]>(`/api/v1/projects/${projectId}/scope-options`);
}

export async function listTasks(projectId: string): Promise<Task[]> {
  return requestJson<Task[]>("/api/v1/tasks", { query: { project_id: projectId } });
}

export async function createTask(input: {
  project_id: string;
  title: string;
  description: string;
  acceptance_criteria?: string[];
  metadata?: Record<string, unknown>;
}): Promise<Task> {
  return requestJson<Task>("/api/v1/tasks", {
    method: "POST",
    body: JSON.stringify({
      project_id: input.project_id,
      title: input.title,
      description: input.description,
      acceptance_criteria: input.acceptance_criteria ?? [],
      metadata: input.metadata ?? {},
    }),
  });
}

export async function listSessions(projectId: string): Promise<Session[]> {
  return requestJson<Session[]>("/api/v1/sessions", { query: { project_id: projectId } });
}

export async function getSession(sessionId: string): Promise<Session> {
  return requestJson<Session>(`/api/v1/sessions/${sessionId}`);
}

export async function createSession(input: {
  projectId: string;
  role: SessionRole;
  taskId?: string | null;
  commandOverride?: string;
  runtimePreference?: string;
  allowSimulationFallback?: boolean;
  simulationMode?: boolean;
  model?: string;
  metadata?: Record<string, unknown>;
}): Promise<Session> {
  return requestJson<Session>("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      project_id: input.projectId,
      role: input.role,
      task_id: input.taskId ?? null,
      command_override: input.commandOverride || null,
      runtime_preference: input.runtimePreference ?? null,
      allow_simulation_fallback: input.allowSimulationFallback ?? null,
      simulation_mode: input.simulationMode ?? null,
      model: input.model ?? null,
      metadata: input.metadata ?? {},
    }),
  });
}

export async function startSession(
  sessionId: string,
  initialMessage?: string,
): Promise<Session> {
  return requestJson<Session>(`/api/v1/sessions/${sessionId}/start`, {
    method: "POST",
    body: JSON.stringify({
      initial_message: initialMessage ?? null,
    }),
  });
}

export async function startProjectExecution(input: {
  projectId: string;
  runtimePreference?: string | null;
  allowSimulationFallback?: boolean | null;
  simulationMode?: boolean | null;
  model?: string | null;
  initialMessage?: string | null;
  metadata?: Record<string, unknown>;
}): Promise<Session> {
  return requestJson<Session>(`/api/v1/projects/${input.projectId}/start`, {
    method: "POST",
    body: JSON.stringify({
      runtime_preference: input.runtimePreference ?? null,
      allow_simulation_fallback: input.allowSimulationFallback ?? null,
      simulation_mode: input.simulationMode ?? null,
      model: input.model ?? null,
      initial_message: input.initialMessage ?? null,
      metadata: input.metadata ?? {},
    }),
  });
}

export async function sendProjectManagerInstruction(input: {
  projectId: string;
  message: string;
  metadata?: Record<string, unknown>;
}): Promise<Session> {
  return requestJson<Session>(`/api/v1/projects/${input.projectId}/manager-instructions`, {
    method: "POST",
    body: JSON.stringify({
      message: input.message,
      metadata: input.metadata ?? {},
    }),
  });
}

export async function sendInstruction(
  sessionId: string,
  message: string,
): Promise<ApiMessage> {
  return requestJson<ApiMessage>(`/api/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export async function interruptSession(sessionId: string): Promise<ApiMessage> {
  return requestJson<ApiMessage>(`/api/v1/sessions/${sessionId}/interrupt`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function stopSession(sessionId: string): Promise<ApiMessage> {
  return requestJson<ApiMessage>(`/api/v1/sessions/${sessionId}/stop`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function assignTask(input: {
  taskId: string;
  sessionId: string;
  allowedPaths: string[];
}): Promise<Task> {
  const response = await requestJson<{ task: Task }>(`/api/v1/tasks/${input.taskId}/assign`, {
    method: "POST",
    body: JSON.stringify({
      session_id: input.sessionId,
      allowed_paths: input.allowedPaths,
    }),
  });
  return response.task;
}

export async function listReviews(projectId: string): Promise<Review[]> {
  return requestJson<Review[]>("/api/v1/reviews", { query: { project_id: projectId } });
}

export async function createReview(input: {
  projectId: string;
  taskId?: string | null;
  sessionId?: string | null;
  requesterSessionId?: string | null;
  reviewerSessionId?: string | null;
  summary?: string;
  lintCommand?: string;
  testCommand?: string;
}): Promise<Review> {
  return requestJson<Review>("/api/v1/reviews", {
    method: "POST",
    body: JSON.stringify({
      project_id: input.projectId,
      task_id: input.taskId ?? null,
      session_id: input.sessionId ?? null,
      requester_session_id: input.requesterSessionId ?? null,
      reviewer_session_id: input.reviewerSessionId ?? null,
      summary: input.summary || null,
      lint_command: input.lintCommand || null,
      test_command: input.testCommand || null,
    }),
  });
}

export async function approveReview(reviewId: string, note: string): Promise<Review> {
  return requestJson<Review>(`/api/v1/reviews/${reviewId}/approve`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
}

export async function rejectReview(input: {
  reviewId: string;
  note: string;
  status?: "needs_changes" | "rejected";
}): Promise<Review> {
  return requestJson<Review>(`/api/v1/reviews/${input.reviewId}/reject`, {
    method: "POST",
    body: JSON.stringify({
      note: input.note,
      status: input.status ?? "needs_changes",
    }),
  });
}

export async function markReviewMergeReady(input: {
  reviewId: string;
  approvedBy: string;
  note: string;
}): Promise<Review> {
  return requestJson<Review>(`/api/v1/reviews/${input.reviewId}/merge-ready`, {
    method: "POST",
    body: JSON.stringify({
      approved_by: input.approvedBy,
      note: input.note,
    }),
  });
}

export async function listProjectEvents(projectId: string, limit = 60): Promise<EventEnvelope[]> {
  return requestJson<EventEnvelope[]>("/api/v1/events", {
    query: { project_id: projectId, limit },
  });
}

export type FileEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size: number | null;
};

export type FileContent = {
  path: string;
  content: string;
  size: number;
  mime_type: string;
};

export async function listProjectFiles(
  projectId: string,
  path = "",
): Promise<FileEntry[]> {
  return requestJson<FileEntry[]>(`/api/v1/projects/${projectId}/files`, {
    query: { path: path || null },
  });
}

export async function getProjectFileContent(
  projectId: string,
  path: string,
): Promise<FileContent> {
  return requestJson<FileContent>(`/api/v1/projects/${projectId}/files/content`, {
    query: { path },
  });
}

export type PreviewInfo = {
  available: boolean;
  entry_file: string | null;
  preview_url: string | null;
  kind: string | null;
};

export type GitHubPushResult = {
  success: boolean;
  repo_url: string | null;
  error: string | null;
};

export async function getPreviewInfo(projectId: string): Promise<PreviewInfo> {
  return requestJson<PreviewInfo>(`/api/v1/projects/${projectId}/files/preview-info`);
}

export async function pushToGitHub(
  projectId: string,
  options?: { repo_name?: string; private?: boolean; org?: string },
): Promise<GitHubPushResult> {
  return requestJson<GitHubPushResult>(`/api/v1/projects/${projectId}/files/push-github`, {
    method: "POST",
    body: JSON.stringify({
      repo_name: options?.repo_name ?? null,
      private: options?.private ?? true,
      org: options?.org ?? null,
    }),
  });
}

export function getRawFileUrl(projectId: string, filePath: string): string {
  return new URL(`/api/v1/projects/${projectId}/files/raw/${filePath}`, apiBaseUrl).toString();
}

export async function triggerOrchestratorTick(projectId: string): Promise<{
  started_at: string;
  completed_at: string;
  projects: Array<{
    project_id: string;
    processed_event_count: number;
    last_event_sequence: number;
    actions: Array<{
      kind: string;
      project_id: string;
      task_id: string | null;
      session_id: string | null;
      detail: string;
      payload: Record<string, unknown>;
    }>;
  }>;
}> {
  return requestJson("/api/v1/orchestrator/tick", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId }),
  });
}
