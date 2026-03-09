"use client";

import {
  type FormEvent,
  useEffect,
  useRef,
  useState,
  useTransition,
} from "react";

import {
  ApiError,
  approveReview,
  assignTask,
  createReview,
  createSession,
  createTask,
  getApiHealth,
  getProjectEventsWebSocketUrl,
  interruptSession,
  listProjectEvents,
  listProjects,
  listReviews,
  listSessions,
  listTasks,
  markReviewMergeReady,
  rejectReview,
  startSession,
  stopSession,
} from "@/lib/api";
import type {
  EventEnvelope,
  HealthResponse,
  Project,
  Review,
  Session,
  SessionRole,
  Task,
} from "@/lib/types";
import { StatusPill, statusToneFor } from "@/components/status-pill";

function pickCurrentId(items: Array<{ id: string }>, currentId: string | null): string | null {
  return items.some((item) => item.id === currentId) ? currentId : (items[0]?.id ?? null);
}

function shortId(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : "none";
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "n/a";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "n/a";
  }

  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return null;
}

function eventSummary(event: EventEnvelope): string {
  const payload = event.payload;
  return (
    firstString(
      payload.detail,
      payload.summary,
      payload.note,
      payload.reason,
      payload.error,
      payload.message,
    ) ?? event.event_type
  );
}

function taskBlockedReason(task: Task): string | null {
  return firstString(task.metadata.orchestrator?.blocked_reason, task.metadata.blocked_reason);
}

function taskAssignedSessionId(task: Task): string | null {
  return task.metadata.orchestrator?.assigned_session_id ?? null;
}

function reviewTitle(review: Review): string {
  const packet = review.review_packet;
  if ("task" in packet && packet.task && typeof packet.task.title === "string") {
    return packet.task.title;
  }
  return shortId(review.id);
}

export function DashboardShell() {
  const [health, setHealth] = useState<HealthResponse>({
    status: "loading",
    service: "Orchestrator API",
    version: "unknown",
    checks: {},
  });
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [events, setEvents] = useState<EventEnvelope[]>([]);

  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [assignmentSessionId, setAssignmentSessionId] = useState<string | null>(null);
  const [selectedReviewId, setSelectedReviewId] = useState<string | null>(null);

  const [taskTitle, setTaskTitle] = useState("");
  const [taskDescription, setTaskDescription] = useState("");
  const [newSessionRole, setNewSessionRole] = useState<SessionRole>("worker");
  const [newSessionTaskId, setNewSessionTaskId] = useState<string | null>(null);
  const [newSessionCommand, setNewSessionCommand] = useState("");
  const [assignPathsInput, setAssignPathsInput] = useState("");
  const [newReviewTaskId, setNewReviewTaskId] = useState<string | null>(null);
  const [newReviewWorkerSessionId, setNewReviewWorkerSessionId] = useState<string | null>(null);
  const [newReviewReviewerSessionId, setNewReviewReviewerSessionId] = useState<string | null>(null);
  const [newReviewSummary, setNewReviewSummary] = useState("");
  const [newReviewLintCommand, setNewReviewLintCommand] = useState("git diff --stat");
  const [newReviewTestCommand, setNewReviewTestCommand] = useState("git status --short");
  const [reviewNote, setReviewNote] = useState("");
  const [reviewRejectNote, setReviewRejectNote] = useState("");
  const [approverName, setApproverName] = useState("");
  const [mergeReadyNote, setMergeReadyNote] = useState("");

  const [flashMessage, setFlashMessage] = useState<{
    tone: "success" | "danger" | "info";
    message: string;
  } | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [socketState, setSocketState] = useState<"idle" | "connecting" | "open" | "closed">(
    "idle",
  );
  const [isPending, startTransition] = useTransition();

  const refreshTimerRef = useRef<number | null>(null);

  const selectedProject =
    projects.find((project) => project.id === selectedProjectId) ?? null;
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null;
  const selectedReview = reviews.find((review) => review.id === selectedReviewId) ?? null;
  const workerSessions = sessions.filter((session) => session.role === "worker");
  const reviewerSessions = sessions.filter((session) => session.role === "reviewer");

  const taskTitleById: Record<string, string> = {};
  for (const task of tasks) {
    taskTitleById[task.id] = task.title;
  }

  const sessionById: Record<string, Session> = {};
  for (const session of sessions) {
    sessionById[session.id] = session;
  }

  async function reloadProjects(): Promise<void> {
    const nextHealth = await getApiHealth();
    setHealth(nextHealth);
    const nextProjects = await listProjects();
    startTransition(() => {
      setProjects(nextProjects);
      setSelectedProjectId((current) => pickCurrentId(nextProjects, current));
    });
  }

  async function reloadProjectData(projectId: string, includeEvents = true): Promise<void> {
    const [nextTasks, nextSessions, nextReviews, nextEvents] = await Promise.all([
      listTasks(projectId),
      listSessions(projectId),
      listReviews(projectId),
      includeEvents ? listProjectEvents(projectId) : Promise.resolve(null),
    ]);

    startTransition(() => {
      setTasks(nextTasks);
      setSessions(nextSessions);
      setReviews(nextReviews);
      if (nextEvents) {
        setEvents(nextEvents);
      }
      setSelectedTaskId((current) => pickCurrentId(nextTasks, current));
      setSelectedSessionId((current) => pickCurrentId(nextSessions, current));
      setSelectedReviewId((current) => pickCurrentId(nextReviews, current));
      const nextWorkerSessions = nextSessions.filter((session) => session.role === "worker");
      const nextReviewerSessions = nextSessions.filter((session) => session.role === "reviewer");
      setAssignmentSessionId((current) =>
        pickCurrentId(nextWorkerSessions, current),
      );
      setNewSessionTaskId((current) => pickCurrentId(nextTasks, current));
      setNewReviewTaskId((current) => pickCurrentId(nextTasks, current));
      setNewReviewWorkerSessionId((current) => pickCurrentId(nextWorkerSessions, current));
      setNewReviewReviewerSessionId((current) => pickCurrentId(nextReviewerSessions, current));
    });
  }

  function scheduleProjectRefresh(projectId: string): void {
    if (refreshTimerRef.current !== null) {
      window.clearTimeout(refreshTimerRef.current);
    }
    refreshTimerRef.current = window.setTimeout(() => {
      void reloadProjectData(projectId, false).catch((error: unknown) => {
        setFlashMessage({
          tone: "danger",
          message: error instanceof Error ? error.message : "Failed to refresh project state.",
        });
      });
    }, 280);
  }

  async function refreshSelectedProject(): Promise<void> {
    await reloadProjects();
    if (selectedProjectId) {
      await reloadProjectData(selectedProjectId);
    }
  }

  async function runAction(
    label: string,
    callback: () => Promise<void>,
  ): Promise<void> {
    setBusyAction(label);
    setFlashMessage(null);

    try {
      await callback();
      setFlashMessage({ tone: "success", message: label });
    } catch (error) {
      setFlashMessage({
        tone: "danger",
        message:
          error instanceof ApiError || error instanceof Error
            ? error.message
            : "Unexpected request failure.",
      });
    } finally {
      setBusyAction(null);
    }
  }

  useEffect(() => {
    void reloadProjects().catch((error: unknown) => {
      setFlashMessage({
        tone: "danger",
        message: error instanceof Error ? error.message : "Failed to load projects.",
      });
    });
  }, []);

  useEffect(() => {
    if (!selectedProjectId) {
      setTasks([]);
      setSessions([]);
      setReviews([]);
      setEvents([]);
      return;
    }

    void reloadProjectData(selectedProjectId).catch((error: unknown) => {
      setFlashMessage({
        tone: "danger",
        message: error instanceof Error ? error.message : "Failed to load project data.",
      });
    });
  }, [selectedProjectId]);

  useEffect(() => {
    if (!selectedProjectId) {
      setSocketState("idle");
      return;
    }

    setSocketState("connecting");
    const socket = new WebSocket(getProjectEventsWebSocketUrl(selectedProjectId));

    socket.onopen = () => {
      setSocketState("open");
    };
    socket.onclose = () => {
      setSocketState("closed");
    };
    socket.onerror = () => {
      setSocketState("closed");
    };
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as EventEnvelope;
        startTransition(() => {
          setEvents((current) => [event, ...current].slice(0, 80));
        });
        scheduleProjectRefresh(selectedProjectId);
      } catch {}
    };

    return () => {
      socket.close();
    };
  }, [selectedProjectId]);

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    setReviewNote(selectedReview?.reviewer_notes ?? "");
    setReviewRejectNote(selectedReview?.reviewer_notes ?? "");
    setApproverName(selectedReview?.approval.human_approved_by ?? "");
    setMergeReadyNote(selectedReview?.approval.note ?? "");
  }, [selectedReviewId, selectedReview?.reviewer_notes, selectedReview?.approval.human_approved_by, selectedReview?.approval.note]);

  async function handleCreateTask(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!selectedProjectId) {
      setFlashMessage({ tone: "danger", message: "Select a project before creating tasks." });
      return;
    }

    await runAction("Task created.", async () => {
      const createdTask = await createTask({
        project_id: selectedProjectId,
        title: taskTitle,
        description: taskDescription,
      });
      setTaskTitle("");
      setTaskDescription("");
      setSelectedTaskId(createdTask.id);
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleCreateSession(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!selectedProjectId) {
      setFlashMessage({ tone: "danger", message: "Select a project before creating sessions." });
      return;
    }
    if (newSessionRole === "worker" && !newSessionTaskId) {
      setFlashMessage({ tone: "danger", message: "Worker sessions require a task." });
      return;
    }

    await runAction("Session created.", async () => {
      const session = await createSession({
        projectId: selectedProjectId,
        role: newSessionRole,
        taskId: newSessionRole === "worker" ? newSessionTaskId : null,
        commandOverride: newSessionCommand.trim() || undefined,
        simulationMode: true,
      });
      setNewSessionCommand("");
      setSelectedSessionId(session.id);
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleAssignTask(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!selectedProjectId || !selectedTaskId || !assignmentSessionId) {
      setFlashMessage({
        tone: "danger",
        message: "Select a project, task, and worker session before assigning.",
      });
      return;
    }

    const allowedPaths = assignPathsInput
      .split(/[\n,]/)
      .map((value) => value.trim())
      .filter(Boolean);

    await runAction("Task assigned.", async () => {
      await assignTask({
        taskId: selectedTaskId,
        sessionId: assignmentSessionId,
        allowedPaths,
      });
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleCreateReview(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!selectedProjectId || !newReviewTaskId || !newReviewWorkerSessionId) {
      setFlashMessage({
        tone: "danger",
        message: "Select a project, task, and worker session before creating a review.",
      });
      return;
    }

    await runAction("Review created.", async () => {
      const review = await createReview({
        projectId: selectedProjectId,
        taskId: newReviewTaskId,
        requesterSessionId: newReviewWorkerSessionId,
        reviewerSessionId: newReviewReviewerSessionId,
        summary: newReviewSummary.trim() || undefined,
        lintCommand: newReviewLintCommand.trim() || undefined,
        testCommand: newReviewTestCommand.trim() || undefined,
      });
      setNewReviewSummary("");
      setSelectedReviewId(review.id);
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleStartSession(sessionId: string): Promise<void> {
    if (!selectedProjectId) {
      return;
    }

    await runAction("Session started.", async () => {
      await startSession(sessionId);
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleInterruptSession(sessionId: string): Promise<void> {
    if (!selectedProjectId) {
      return;
    }

    await runAction("Interrupt sent.", async () => {
      await interruptSession(sessionId);
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleStopSession(sessionId: string): Promise<void> {
    if (!selectedProjectId) {
      return;
    }

    await runAction("Stop requested.", async () => {
      await stopSession(sessionId);
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleApproveReview(): Promise<void> {
    if (!selectedProjectId || !selectedReviewId) {
      return;
    }

    await runAction("Review approved.", async () => {
      await approveReview(selectedReviewId, reviewNote);
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleRejectReview(status: "needs_changes" | "rejected"): Promise<void> {
    if (!selectedProjectId || !selectedReviewId) {
      return;
    }

    await runAction(status === "rejected" ? "Review rejected." : "Review marked needs changes.", async () => {
      await rejectReview({
        reviewId: selectedReviewId,
        note: reviewRejectNote || "Operator requested changes.",
        status,
      });
      await reloadProjectData(selectedProjectId);
    });
  }

  async function handleMarkMergeReady(): Promise<void> {
    if (!selectedProjectId || !selectedReviewId) {
      return;
    }

    await runAction("Review marked merge-ready.", async () => {
      await markReviewMergeReady({
        reviewId: selectedReviewId,
        approvedBy: approverName,
        note: mergeReadyNote,
      });
      await reloadProjectData(selectedProjectId);
    });
  }

  return (
    <main className="console-shell">
      <section className="console-hero">
        <div>
          <div className="eyebrow">Operator Console</div>
          <h1>Local Agent Orchestrator</h1>
          <p className="hero-copy">
            Monitor projects, steer session lifecycles, watch live events, and move completed
            worker output through reviewer analysis and human approval.
          </p>
        </div>
        <div className="hero-actions">
          <StatusPill tone={statusToneFor(health.status)}>
            API {health.status}
          </StatusPill>
          <StatusPill tone={socketState === "open" ? "success" : socketState === "connecting" ? "info" : "warning"}>
            Feed {socketState}
          </StatusPill>
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              void refreshSelectedProject();
            }}
            disabled={isPending || busyAction !== null}
          >
            Refresh
          </button>
        </div>
      </section>

      <section className="metric-strip">
        <article className="metric-card">
          <span className="metric-label">Projects</span>
          <strong>{projects.length}</strong>
          <small>{selectedProject?.name ?? "No project selected"}</small>
        </article>
        <article className="metric-card">
          <span className="metric-label">Tasks</span>
          <strong>{tasks.length}</strong>
          <small>{tasks.filter((task) => task.status === "blocked").length} blocked</small>
        </article>
        <article className="metric-card">
          <span className="metric-label">Sessions</span>
          <strong>{sessions.length}</strong>
          <small>{sessions.filter((session) => session.status === "running").length} running</small>
        </article>
        <article className="metric-card">
          <span className="metric-label">Reviews</span>
          <strong>{reviews.length}</strong>
          <small>
            {reviews.filter((review) => review.approval.merge_ready).length} merge-ready
          </small>
        </article>
      </section>

      {flashMessage ? (
        <div className={`flash flash-${flashMessage.tone}`}>{flashMessage.message}</div>
      ) : null}

      <div className="console-grid">
        <aside className="column-stack">
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Projects</h2>
                <p>Choose the project scope for tasks, sessions, events, and reviews.</p>
              </div>
            </div>
            <div className="project-list">
              {projects.length > 0 ? (
                projects.map((project) => (
                  <button
                    key={project.id}
                    type="button"
                    className={`entity-row ${selectedProjectId === project.id ? "is-active" : ""}`}
                    onClick={() => {
                      setSelectedProjectId(project.id);
                      setFlashMessage(null);
                    }}
                  >
                    <div className="entity-top">
                      <strong>{project.name}</strong>
                      <StatusPill tone={statusToneFor(project.status)} compact>
                        {project.status}
                      </StatusPill>
                    </div>
                    <div className="entity-meta">
                      <span>{project.slug}</span>
                      <code>{project.default_branch}</code>
                    </div>
                    <div className="entity-foot">{project.repo_path}</div>
                  </button>
                ))
              ) : (
                <EmptyState
                  title="No projects yet"
                  detail="Demo seeding should populate this automatically; if you disabled it, create a project through the backend API and refresh."
                />
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Actions</h2>
                <p>Create tasks, sessions, assignments, and review packages without leaving the console.</p>
              </div>
            </div>

            <div className="action-block">
              <div className="action-block-head">
                <strong>Create task</strong>
                <span>Seed additional scoped work inside the selected project.</span>
              </div>
              <form className="form-stack" onSubmit={handleCreateTask}>
                <label className="field">
                  <span>Title</span>
                  <input
                    value={taskTitle}
                    onChange={(event) => setTaskTitle(event.target.value)}
                    placeholder="Implement review panel polish"
                    required
                  />
                </label>
                <label className="field">
                  <span>Description</span>
                  <textarea
                    value={taskDescription}
                    onChange={(event) => setTaskDescription(event.target.value)}
                    rows={3}
                    placeholder="Keep it scoped and explicit."
                  />
                </label>
                <button
                  className="primary-button"
                  type="submit"
                  disabled={!selectedProjectId || !taskTitle.trim() || busyAction !== null}
                >
                  Create task
                </button>
              </form>
            </div>

            <div className="action-block">
              <div className="action-block-head">
                <strong>Create session</strong>
                <span>Spawn a manager, worker, or reviewer session entry for the selected project.</span>
              </div>
              <form className="form-stack" onSubmit={handleCreateSession}>
                <label className="field">
                  <span>Role</span>
                  <select
                    value={newSessionRole}
                    onChange={(event) => setNewSessionRole(event.target.value as SessionRole)}
                  >
                    <option value="worker">worker</option>
                    <option value="reviewer">reviewer</option>
                    <option value="manager">manager</option>
                  </select>
                </label>
                <label className="field">
                  <span>Task</span>
                  <select
                    value={newSessionTaskId ?? ""}
                    onChange={(event) => setNewSessionTaskId(event.target.value || null)}
                    disabled={newSessionRole !== "worker"}
                  >
                    <option value="">
                      {newSessionRole === "worker" ? "Choose a task" : "Not required for this role"}
                    </option>
                    {tasks.map((task) => (
                      <option key={task.id} value={task.id}>
                        {task.title} · {task.status}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Command override</span>
                  <input
                    value={newSessionCommand}
                    onChange={(event) => setNewSessionCommand(event.target.value)}
                    placeholder="codex worker --scope frontend"
                  />
                </label>
                <button
                  className="secondary-button"
                  type="submit"
                  disabled={
                    !selectedProjectId ||
                    busyAction !== null ||
                    (newSessionRole === "worker" && !newSessionTaskId)
                  }
                >
                  Create session
                </button>
              </form>
            </div>

            <div className="action-block">
              <div className="action-block-head">
                <strong>Assign task</strong>
                <span>Bind the selected task to an existing worker session and scope its allowed paths.</span>
              </div>
              <form className="form-stack" onSubmit={handleAssignTask}>
                <div className="field">
                  <span>Selected task</span>
                  <div className="readout">
                    {selectedTask ? selectedTask.title : "Choose a task from the list"}
                  </div>
                </div>
                <label className="field">
                  <span>Assign worker session</span>
                  <select
                    value={assignmentSessionId ?? ""}
                    onChange={(event) => setAssignmentSessionId(event.target.value || null)}
                  >
                    <option value="">Choose a worker session</option>
                    {workerSessions.map((session) => (
                      <option key={session.id} value={session.id}>
                        {shortId(session.id)} · {taskTitleById[session.task_id ?? ""] ?? "unscoped"} · {session.status}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Allowed paths</span>
                  <textarea
                    value={assignPathsInput}
                    onChange={(event) => setAssignPathsInput(event.target.value)}
                    rows={3}
                    placeholder="backend/app/services&#10;frontend/components"
                  />
                </label>
                <button
                  className="secondary-button"
                  type="submit"
                  disabled={!selectedTask || !assignmentSessionId || busyAction !== null}
                >
                  Assign task
                </button>
              </form>
            </div>

            <div className="action-block">
              <div className="action-block-head">
                <strong>Create review</strong>
                <span>Package diff, lint, and test results for reviewer and human approval flow.</span>
              </div>
              <form className="form-stack" onSubmit={handleCreateReview}>
                <label className="field">
                  <span>Task</span>
                  <select
                    value={newReviewTaskId ?? ""}
                    onChange={(event) => setNewReviewTaskId(event.target.value || null)}
                  >
                    <option value="">Choose a task</option>
                    {tasks.map((task) => (
                      <option key={task.id} value={task.id}>
                        {task.title} · {task.status}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Worker session</span>
                  <select
                    value={newReviewWorkerSessionId ?? ""}
                    onChange={(event) => setNewReviewWorkerSessionId(event.target.value || null)}
                  >
                    <option value="">Choose a worker session</option>
                    {workerSessions.map((session) => (
                      <option key={session.id} value={session.id}>
                        {shortId(session.id)} · {taskTitleById[session.task_id ?? ""] ?? "unscoped"} · {session.status}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Reviewer session</span>
                  <select
                    value={newReviewReviewerSessionId ?? ""}
                    onChange={(event) => setNewReviewReviewerSessionId(event.target.value || null)}
                  >
                    <option value="">Optional reviewer session</option>
                    {reviewerSessions.map((session) => (
                      <option key={session.id} value={session.id}>
                        {shortId(session.id)} · {session.status}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span>Summary</span>
                  <textarea
                    value={newReviewSummary}
                    onChange={(event) => setNewReviewSummary(event.target.value)}
                    rows={2}
                    placeholder="Ready for reviewer pass."
                  />
                </label>
                <label className="field">
                  <span>Lint command</span>
                  <input
                    value={newReviewLintCommand}
                    onChange={(event) => setNewReviewLintCommand(event.target.value)}
                    placeholder="git diff --stat"
                  />
                </label>
                <label className="field">
                  <span>Test command</span>
                  <input
                    value={newReviewTestCommand}
                    onChange={(event) => setNewReviewTestCommand(event.target.value)}
                    placeholder="git status --short"
                  />
                </label>
                <button
                  className="secondary-button"
                  type="submit"
                  disabled={
                    !selectedProjectId ||
                    !newReviewTaskId ||
                    !newReviewWorkerSessionId ||
                    busyAction !== null
                  }
                >
                  Create review
                </button>
              </form>
            </div>
          </section>
        </aside>

        <section className="column-stack">
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Tasks</h2>
                <p>Track status, ownership, and block reasons at a glance.</p>
              </div>
            </div>
            <div className="list-grid">
              {tasks.length > 0 ? (
                tasks.map((task) => {
                  const assignedSessionId = taskAssignedSessionId(task);
                  const assignedSession = assignedSessionId ? sessionById[assignedSessionId] : null;
                  const blockedReason = taskBlockedReason(task);

                  return (
                    <button
                      key={task.id}
                      type="button"
                      className={`entity-row ${selectedTaskId === task.id ? "is-active" : ""}`}
                      onClick={() => setSelectedTaskId(task.id)}
                    >
                      <div className="entity-top">
                        <strong>{task.title}</strong>
                        <StatusPill tone={statusToneFor(task.status)} compact>
                          {task.status}
                        </StatusPill>
                      </div>
                      <p className="entity-copy">{task.description || "No description provided."}</p>
                      <div className="entity-meta">
                        <span>Owner {assignedSession ? shortId(assignedSession.id) : "unassigned"}</span>
                        <span>
                          Paths {task.metadata.orchestrator?.allowed_paths?.length ?? 0}
                        </span>
                      </div>
                      {blockedReason ? (
                        <div className="entity-alert">Blocked: {blockedReason}</div>
                      ) : null}
                    </button>
                  );
                })
              ) : (
                <EmptyState
                  title="No tasks in this project"
                  detail="Create a task from the action panel to start populating the console."
                />
              )}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>Sessions</h2>
                <p>See which task each session owns and trigger lifecycle controls.</p>
              </div>
            </div>
            <div className="list-grid">
              {sessions.length > 0 ? (
                sessions.map((session) => {
                  const isActive = ["starting", "running", "blocked"].includes(session.status);

                  return (
                    <article
                      key={session.id}
                      className={`entity-row session-row ${selectedSessionId === session.id ? "is-active" : ""}`}
                    >
                      <button
                        type="button"
                        className="row-select"
                        onClick={() => setSelectedSessionId(session.id)}
                      >
                        <div className="entity-top">
                          <strong>{session.role}</strong>
                          <StatusPill tone={statusToneFor(session.status)} compact>
                            {session.status}
                          </StatusPill>
                        </div>
                        <div className="entity-meta">
                          <span>Task {taskTitleById[session.task_id ?? ""] ?? "none"}</span>
                          <code>{shortId(session.id)}</code>
                        </div>
                        <div className="entity-foot">
                          {session.blocked_reason
                            ? `Blocked: ${session.blocked_reason}`
                            : `Heartbeat ${formatDateTime(session.last_heartbeat_at)}`}
                        </div>
                      </button>
                      <div className="row-action-strip">
                        {session.status === "pending" ? (
                          <button
                            className="ghost-button"
                            type="button"
                            onClick={() => {
                              void handleStartSession(session.id);
                            }}
                            disabled={busyAction !== null}
                          >
                            Start
                          </button>
                        ) : null}
                        {isActive ? (
                          <button
                            className="ghost-button"
                            type="button"
                            onClick={() => {
                              void handleInterruptSession(session.id);
                            }}
                            disabled={busyAction !== null}
                          >
                            Interrupt
                          </button>
                        ) : null}
                        {isActive ? (
                          <button
                            className="danger-button"
                            type="button"
                            onClick={() => {
                              void handleStopSession(session.id);
                            }}
                            disabled={busyAction !== null}
                          >
                            Stop
                          </button>
                        ) : null}
                      </div>
                    </article>
                  );
                })
              ) : (
                <EmptyState
                  title="No sessions yet"
                  detail="Create a session from the action panel to start driving tasks through the runtime layer."
                />
              )}
            </div>
          </section>
        </section>

        <aside className="column-stack">
          <section className="panel review-panel">
            <div className="panel-head">
              <div>
                <h2>Review detail</h2>
                <p>Inspect diff summary, checks, notes, and approval state in one place.</p>
              </div>
            </div>

            <div className="review-list">
              {reviews.map((review) => (
                <button
                  key={review.id}
                  type="button"
                  className={`review-chip ${selectedReviewId === review.id ? "is-active" : ""}`}
                  onClick={() => {
                    setSelectedReviewId(review.id);
                    setReviewNote(review.reviewer_notes ?? "");
                    setReviewRejectNote(review.reviewer_notes ?? "");
                    setMergeReadyNote(review.approval.note ?? "");
                  }}
                >
                  <span>{reviewTitle(review)}</span>
                  <StatusPill tone={statusToneFor(review.status)} compact>
                    {review.status}
                  </StatusPill>
                </button>
              ))}
            </div>

            {selectedReview ? (
              <div className="review-detail">
                <div className="detail-header">
                  <div>
                    <h3>{selectedReview.summary ?? "Review package"}</h3>
                    <p>
                      Task {shortId(selectedReview.task_id)} · Reviewer {shortId(selectedReview.reviewer_session_id)}
                    </p>
                  </div>
                  <StatusPill tone={statusToneFor(selectedReview.status)}>
                    {selectedReview.status}
                  </StatusPill>
                </div>

                <div className="detail-grid">
                  <article className="detail-card">
                    <div className="detail-card-head">
                      <span>Diff</span>
                      <strong>{selectedReview.diff.summary}</strong>
                    </div>
                    <div className="token-list">
                      {selectedReview.diff.changed_files.map((file) => (
                        <code key={file}>{file}</code>
                      ))}
                    </div>
                    <pre className="diff-preview">{selectedReview.diff.diff_preview || "No diff preview."}</pre>
                  </article>

                  <article className="detail-card">
                    <div className="detail-card-head">
                      <span>Checks</span>
                      <strong>Lint and tests</strong>
                    </div>
                    <div className="check-grid">
                      <CheckCard title="Lint" check={selectedReview.lint} />
                      <CheckCard title="Tests" check={selectedReview.tests} />
                    </div>
                  </article>

                  <article className="detail-card">
                    <div className="detail-card-head">
                      <span>Approval</span>
                      <strong>
                        {selectedReview.approval.merge_ready
                          ? "Merge-ready"
                          : selectedReview.approval.human_approved
                            ? "Human approved"
                            : "Awaiting decision"}
                      </strong>
                    </div>
                    <div className="approval-grid">
                      <div>
                        <span className="mini-label">Reviewer</span>
                        <StatusPill tone={statusToneFor(selectedReview.approval.reviewer_status)} compact>
                          {selectedReview.approval.reviewer_status}
                        </StatusPill>
                      </div>
                      <div>
                        <span className="mini-label">Human</span>
                        <div className="readout">
                          {selectedReview.approval.human_approved_by
                            ? `${selectedReview.approval.human_approved_by} · ${formatDateTime(selectedReview.approval.human_approved_at)}`
                            : "Pending"}
                        </div>
                      </div>
                    </div>
                  </article>
                </div>

                <div className="form-stack review-actions">
                  <label className="field">
                    <span>Reviewer note</span>
                    <textarea
                      value={reviewNote}
                      onChange={(event) => setReviewNote(event.target.value)}
                      rows={3}
                      placeholder="Summarize the review outcome."
                    />
                  </label>
                  <div className="button-row">
                    <button
                      className="primary-button"
                      type="button"
                      onClick={() => {
                        void handleApproveReview();
                      }}
                      disabled={busyAction !== null}
                    >
                      Approve
                    </button>
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => {
                        void handleRejectReview("needs_changes");
                      }}
                      disabled={busyAction !== null}
                    >
                      Needs changes
                    </button>
                    <button
                      className="danger-button"
                      type="button"
                      onClick={() => {
                        void handleRejectReview("rejected");
                      }}
                      disabled={busyAction !== null}
                    >
                      Reject
                    </button>
                  </div>
                  <label className="field">
                    <span>Reject note</span>
                    <textarea
                      value={reviewRejectNote}
                      onChange={(event) => setReviewRejectNote(event.target.value)}
                      rows={2}
                      placeholder="What should change before this can move forward?"
                    />
                  </label>
                  <label className="field">
                    <span>Human approval</span>
                    <input
                      value={approverName}
                      onChange={(event) => setApproverName(event.target.value)}
                      placeholder="human@example.com"
                    />
                  </label>
                  <label className="field">
                    <span>Merge-ready note</span>
                    <textarea
                      value={mergeReadyNote}
                      onChange={(event) => setMergeReadyNote(event.target.value)}
                      rows={2}
                      placeholder="Approved for merge queue."
                    />
                  </label>
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={() => {
                      void handleMarkMergeReady();
                    }}
                    disabled={
                      busyAction !== null ||
                      selectedReview.status !== "approved" ||
                      !approverName.trim()
                    }
                  >
                    Mark merge-ready
                  </button>
                </div>
              </div>
            ) : (
              <EmptyState
                title="No review selected"
                detail="Create or select a review to inspect diff summaries, check results, and approval controls."
              />
            )}
          </section>
        </aside>
      </div>

      <section className="panel feed-panel">
        <div className="panel-head">
          <div>
            <h2>Live event feed</h2>
            <p>Project-scoped websocket events stream here and trigger background data refreshes.</p>
          </div>
        </div>
        <div className="event-feed">
          {events.length > 0 ? (
            events.map((event) => (
              <article className="event-row" key={event.id}>
                <div className="event-meta">
                  <StatusPill tone={statusToneFor(event.level)} compact>
                    {event.level}
                  </StatusPill>
                  <code>{event.event_type}</code>
                  <span>{formatDateTime(event.occurred_at)}</span>
                </div>
                <strong>{eventSummary(event)}</strong>
                <div className="entity-meta">
                  <span>{event.source.kind}</span>
                  <span>task {shortId(event.task_id)}</span>
                  <span>session {shortId(event.session_id)}</span>
                </div>
              </article>
            ))
          ) : (
            <EmptyState
              title="No events yet"
              detail="Once the selected project emits events, they will appear here live."
            />
          )}
        </div>
      </section>
    </main>
  );
}

function CheckCard({
  title,
  check,
}: {
  title: string;
  check: Review["lint"] | Review["tests"];
}) {
  return (
    <div className="check-card">
      <div className="detail-card-head">
        <span>{title}</span>
        <StatusPill tone={statusToneFor(check?.status)} compact>
          {check?.status ?? "not run"}
        </StatusPill>
      </div>
      <div className="entity-foot">
        {check?.summary ?? "No command was captured for this check."}
      </div>
      {check?.command ? <code>{check.command}</code> : null}
    </div>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  );
}
