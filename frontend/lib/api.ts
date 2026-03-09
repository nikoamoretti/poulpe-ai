import type {
  ApiMessage,
  EventEnvelope,
  HealthResponse,
  Project,
  Review,
  Session,
  SessionRole,
  Task,
} from "@/lib/types";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  process.env.INTERNAL_API_BASE_URL ??
  "http://localhost:8000";

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
  const response = await fetch(buildUrl(path, query), {
    ...init,
    cache: init.method && init.method !== "GET" ? "no-store" : "no-store",
    headers: {
      "Content-Type": "application/json",
      ...headers,
    },
  });

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

export async function listProjects(): Promise<Project[]> {
  return requestJson<Project[]>("/api/v1/projects");
}

export async function listTasks(projectId: string): Promise<Task[]> {
  return requestJson<Task[]>("/api/v1/tasks", { query: { project_id: projectId } });
}

export async function createTask(input: {
  project_id: string;
  title: string;
  description: string;
  acceptance_criteria?: string[];
}): Promise<Task> {
  return requestJson<Task>("/api/v1/tasks", {
    method: "POST",
    body: JSON.stringify({
      project_id: input.project_id,
      title: input.title,
      description: input.description,
      acceptance_criteria: input.acceptance_criteria ?? [],
    }),
  });
}

export async function listSessions(projectId: string): Promise<Session[]> {
  return requestJson<Session[]>("/api/v1/sessions", { query: { project_id: projectId } });
}

export async function createSession(input: {
  projectId: string;
  role: SessionRole;
  taskId?: string | null;
  commandOverride?: string;
  simulationMode?: boolean;
}): Promise<Session> {
  return requestJson<Session>("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({
      project_id: input.projectId,
      role: input.role,
      task_id: input.taskId ?? null,
      command_override: input.commandOverride || null,
      simulation_mode: input.simulationMode ?? true,
    }),
  });
}

export async function startSession(sessionId: string): Promise<Session> {
  return requestJson<Session>(`/api/v1/sessions/${sessionId}/start`, {
    method: "POST",
    body: JSON.stringify({}),
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
