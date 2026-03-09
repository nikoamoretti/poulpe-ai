export type HealthResponse = {
  status: string;
  service: string;
  version: string;
  checks: Record<string, string>;
};

export type ProjectStatus = "active" | "archived";
export type TaskStatus =
  | "pending"
  | "in_progress"
  | "blocked"
  | "review"
  | "done"
  | "canceled";
export type SessionRole = "manager" | "worker" | "reviewer";
export type SessionStatus =
  | "pending"
  | "starting"
  | "running"
  | "blocked"
  | "completed"
  | "failed"
  | "stopped";
export type ReviewStatus =
  | "pending"
  | "running"
  | "needs_changes"
  | "approved"
  | "rejected";
export type EventLevel = "debug" | "info" | "warn" | "error";
export type ArtifactKind =
  | "diff"
  | "patch"
  | "test_report"
  | "lint_report"
  | "session_log"
  | "transcript"
  | "bundle"
  | "note";

export type Project = {
  id: string;
  name: string;
  slug: string;
  repo_path: string;
  default_branch: string;
  status: ProjectStatus;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type TaskOrchestratorState = {
  assigned_session_id?: string;
  allowed_paths?: string[];
  dependency_task_ids?: string[];
  blocked_reason?: string | null;
  conflicts?: Array<{
    kind: string;
    detail: string;
    payload: Record<string, unknown>;
  }>;
  completion_summary?: string | null;
};

export type Task = {
  id: string;
  project_id: string;
  parent_task_id: string | null;
  title: string;
  description: string;
  status: TaskStatus;
  priority: number;
  acceptance_criteria: string[];
  metadata: Record<string, unknown> & {
    orchestrator?: TaskOrchestratorState;
  };
  created_at: string;
  updated_at: string;
};

export type Session = {
  id: string;
  project_id: string;
  task_id: string | null;
  supervisor_session_id: string | null;
  role: SessionRole;
  status: SessionStatus;
  transport: string;
  adapter_kind: string;
  branch_name: string | null;
  workspace_path: string | null;
  command: string | null;
  pid: number | null;
  exit_code: number | null;
  blocked_reason: string | null;
  metadata: Record<string, unknown>;
  runtime_metadata: Record<string, unknown>;
  started_at: string | null;
  ended_at: string | null;
  last_heartbeat_at: string | null;
  created_at: string;
  updated_at: string;
};

export type EventEnvelope = {
  id: string;
  version: string;
  sequence: number;
  category: string;
  event_type: string;
  level: EventLevel;
  source: {
    kind: string;
    role?: SessionRole | null;
    id: string;
  };
  project_id: string | null;
  task_id: string | null;
  session_id: string | null;
  correlation_id: string | null;
  causation_id: string | null;
  occurred_at: string;
  payload: Record<string, unknown>;
  raw_output: string | null;
};

export type ReviewArtifact = {
  id: string;
  kind: ArtifactKind;
  uri: string;
  content_type: string;
  size_bytes: number | null;
  metadata: Record<string, unknown>;
};

export type ReviewDiffSummary = {
  artifact_id: string | null;
  summary: string;
  changed_files: string[];
  diff_preview: string;
};

export type ReviewCheck = {
  artifact_id: string | null;
  command: string | null;
  status: string | null;
  returncode: number | null;
  timed_out: boolean;
  duration_ms: number | null;
  summary: string | null;
};

export type ReviewApproval = {
  reviewer_status: ReviewStatus;
  human_approved: boolean;
  human_approved_by: string | null;
  human_approved_at: string | null;
  merge_ready: boolean;
  merge_ready_by: string | null;
  merge_ready_at: string | null;
  note: string | null;
};

export type ReviewPacket = {
  review_id: string;
  project_id: string;
  task: {
    id: string;
    title: string;
    description: string;
    acceptance_criteria: string[];
  };
  worker_session: {
    id: string;
    branch_name: string | null;
    workspace_path: string | null;
  };
  reviewer_session: string | null;
  workspace: {
    id: string;
    branch_name: string;
    base_branch: string;
    base_commit: string;
    head_commit: string | null;
  };
  diff: {
    summary: {
      summary: string;
      file_count: number;
      changed_files: string[];
      diff_preview: string;
    };
    changed_files: string[];
    diff: string;
  };
  lint: ReviewCommandPacket | null;
  tests: ReviewCommandPacket | null;
  prompt_template_path: string;
  prompt_template: string;
};

export type ReviewCommandPacket = {
  kind: string;
  command: string;
  returncode: number;
  timed_out: boolean;
  duration_ms: number;
  stdout: string;
  stderr: string;
  changed_files: string[];
  status: string;
  summary: string;
};

export type Review = {
  id: string;
  project_id: string;
  task_id: string;
  requester_session_id: string | null;
  reviewer_session_id: string | null;
  status: ReviewStatus;
  summary: string | null;
  reviewer_notes: string | null;
  prompt_template_path: string | null;
  review_packet: ReviewPacket | Record<string, never>;
  diff: ReviewDiffSummary;
  lint: ReviewCheck | null;
  tests: ReviewCheck | null;
  approval: ReviewApproval;
  artifacts: ReviewArtifact[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ApiMessage = {
  detail: string;
  generated_at: string | null;
};
