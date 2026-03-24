"use client";

import { type FormEvent, useEffect, useState, useTransition } from "react";

import { StatusPill, statusToneFor } from "@/components/status-pill";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  createPortfolio,
  createProject,
  getApiHealth,
  getPreviewInfo,
  getProjectFileContent,
  getSession,
  listPortfolioInbox,
  listPortfolios,
  listProjectEvents,
  listProjectFiles,
  listProjects,
  listSessions,
  pushToGitHub,
  respondToPortfolioCheckpoint,
  sendProjectManagerInstruction,
  startPortfolioManager,
  startProjectExecution,
} from "@/lib/api";
import type {
  FileContent,
  FileEntry,
  GitHubPushResult,
  PreviewInfo,
} from "@/lib/api";
import type {
  EventEnvelope,
  HealthResponse,
  Portfolio,
  Project,
  ProjectCheckpoint,
  ProjectCheckpointAction,
  Session,
} from "@/lib/types";

type Flash = { tone: "success" | "danger" | "info"; message: string } | null;
type MainView = "workspace" | "inbox" | "activity" | "deliverables";

type PortfolioBoardData = {
  projects: Project[];
  inbox: ProjectCheckpoint[];
  sessionsByProject: Record<string, Session[]>;
  managerSession: Session | null;
  suggestedProjectId: string | null;
  events: EventEnvelope[];
};

function friendlyTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const diff = Date.now() - date.getTime();
  if (diff < 60_000) return "now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function shortId(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : "";
}

function compactPath(value: string | null | undefined): string {
  if (!value) return "";
  const parts = value.split("/").filter(Boolean);
  if (parts.length <= 2) return value;
  return `${parts.at(-2)}/${parts.at(-1)}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function choosePortfolioId(portfolios: Portfolio[], currentId: string | null): string | null {
  if (currentId && portfolios.some((portfolio) => portfolio.id === currentId)) {
    return currentId;
  }
  return portfolios[0]?.id ?? null;
}

function chooseProjectId(
  projects: Project[],
  inbox: ProjectCheckpoint[],
  currentId: string | null,
): string | null {
  if (currentId && projects.some((project) => project.id === currentId)) {
    return currentId;
  }
  const fromInbox = inbox[0]?.project_id;
  if (fromInbox && projects.some((project) => project.id === fromInbox)) {
    return fromInbox;
  }
  return projects[0]?.id ?? null;
}

function primaryWorkerSession(project: Project, sessions: Session[]): Session | null {
  if (project.worker_session_id) {
    const exact = sessions.find((session) => session.id === project.worker_session_id);
    if (exact) return exact;
  }
  return (
    sessions
      .filter((session) => session.role === "worker" && session.task_id === null)
      .sort(
        (left, right) =>
          new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
      )[0] ?? null
  );
}

function eventTitle(event: EventEnvelope): string {
  const labels: Record<string, string> = {
    "portfolio.created": "Portfolio created",
    "portfolio.manager_started": "Manager started",
    "project.created": "Project created",
    "project.execution_started": "Project started",
    "project.checkpoint_opened": "Checkpoint opened",
    "project.checkpoint_resolved": "Checkpoint resolved",
    "session.start": "Session booted",
    "session.started": "Session started",
    "session.progress": "Progress",
    "session.question": "Question raised",
    "session.blocked": "Blocked",
    "session.tests_run": "Verification ran",
    "session.complete": "Completion claimed",
    "session.completed": "Session completed",
    "session.error": "Session error",
    "session.failed": "Session failed",
    "worktree.ready": "Workspace ready",
    "portfolio.planning_turn_started": "Planning started",
    "portfolio.planning_decomposed": "Goal decomposed",
    "portfolio.planning_single_project": "Single project created",
  };
  return labels[event.event_type] ?? event.event_type.replaceAll(".", " ");
}

function eventDetail(event: EventEnvelope): string {
  const payload = asRecord(event.payload);
  return (
    asString(payload.summary) ??
    asString(payload.detail) ??
    asString(payload.note) ??
    asString(payload.reason) ??
    asString(payload.message) ??
    ""
  );
}

function checkpointActionLabel(action: ProjectCheckpointAction): string {
  if (action === "answer") return "Answer";
  if (action === "approve") return "Approve";
  if (action === "request_changes") return "Request changes";
  return "Dismiss";
}

async function fetchBootstrap(): Promise<{
  health: HealthResponse;
  portfolios: Portfolio[];
}> {
  const [health, portfolios] = await Promise.all([getApiHealth(), listPortfolios()]);
  return { health, portfolios };
}

async function fetchPortfolioBoard(
  portfolio: Portfolio,
  currentProjectId: string | null,
): Promise<PortfolioBoardData> {
  const [projects, inbox, managerSession] = await Promise.all([
    listProjects(portfolio.id),
    listPortfolioInbox(portfolio.id),
    portfolio.manager_session_id ? getSession(portfolio.manager_session_id).catch(() => null) : Promise.resolve(null),
  ]);

  const sessionEntries = await Promise.all(
    projects.map(async (project) => [project.id, await listSessions(project.id)] as const),
  );
  const sessionsByProject = Object.fromEntries(sessionEntries);
  const suggestedProjectId = chooseProjectId(projects, inbox, currentProjectId);
  const events = suggestedProjectId
    ? await listProjectEvents(suggestedProjectId, 50)
    : [];

  return {
    projects,
    inbox,
    sessionsByProject,
    managerSession,
    suggestedProjectId,
    events,
  };
}

export function DashboardShell() {
  const [health, setHealth] = useState<HealthResponse>({
    status: "loading",
    service: "Local Agent Workspace Console",
    version: "",
    checks: {},
  });
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [selectedPortfolioId, setSelectedPortfolioId] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [sessionsByProject, setSessionsByProject] = useState<Record<string, Session[]>>({});
  const [managerSession, setManagerSession] = useState<Session | null>(null);
  const [inbox, setInbox] = useState<ProjectCheckpoint[]>([]);
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [flash, setFlash] = useState<Flash>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [mainView, setMainView] = useState<MainView>("workspace");
  const [portfolioForm, setPortfolioForm] = useState({ name: "", goal: "" });
  const [projectForm, setProjectForm] = useState({
    name: "",
    repoPath: "",
    createRepo: true,
    defaultBranch: "main",
    objective: "",
  });
  const [instructionDraft, setInstructionDraft] = useState("");
  const [checkpointDrafts, setCheckpointDrafts] = useState<Record<string, string>>({});
  const [deliverableFiles, setDeliverableFiles] = useState<FileEntry[]>([]);
  const [deliverablePath, setDeliverablePath] = useState("");
  const [openFile, setOpenFile] = useState<FileContent | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);
  const [previewInfo, setPreviewInfo] = useState<PreviewInfo | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [ghPushResult, setGhPushResult] = useState<GitHubPushResult | null>(null);
  const [pushingGh, setPushingGh] = useState(false);
  const [, startTransition] = useTransition();
  const [showNewProject, setShowNewProject] = useState(false);
  const [showNewPortfolio, setShowNewPortfolio] = useState(false);

  const selectedPortfolio =
    portfolios.find((portfolio) => portfolio.id === selectedPortfolioId) ?? null;
  const selectedProject =
    projects.find((project) => project.id === selectedProjectId) ?? null;
  const selectedProjectSessions = selectedProject
    ? sessionsByProject[selectedProject.id] ?? []
    : [];
  const selectedWorker =
    selectedProject != null ? primaryWorkerSession(selectedProject, selectedProjectSessions) : null;
  const portfolioProjectCount = projects.length;
  const openCheckpointCount = inbox.length;
  const activeWorkerCount = projects.reduce((count, project) => {
    const worker = primaryWorkerSession(project, sessionsByProject[project.id] ?? []);
    if (!worker) return count;
    return count + (["running", "starting", "blocked"].includes(worker.status) ? 1 : 0);
  }, 0);
  const selectedProjectCheckpointCount = selectedProject
    ? inbox.filter((checkpoint) => checkpoint.project_id === selectedProject.id).length
    : 0;

  let nextActionTitle = "Create a portfolio";
  let nextActionDetail = "Start by creating a portfolio to group your projects.";
  if (selectedPortfolio) {
    nextActionTitle = "Add a project";
    nextActionDetail = "Create a project from a name — Poulpe will set up its local repo.";
  }
  if (selectedPortfolio && portfolioProjectCount > 0) {
    nextActionTitle = "Start manager";
    nextActionDetail = "The manager session supervises workers, answers questions, and reviews completions.";
  }
  if (selectedPortfolio && portfolioProjectCount > 0 && managerSession) {
    nextActionTitle = "Start a project";
    nextActionDetail = selectedProject
      ? `Launch ${selectedProject.name} to begin its worker session.`
      : "Pick a project, then start its worker session.";
  }
  if (selectedPortfolio && selectedWorker && selectedWorker.status !== "pending") {
    nextActionTitle = openCheckpointCount > 0 ? "Inbox active" : "Monitor project";
    nextActionDetail =
      openCheckpointCount > 0
        ? "Manager is autonomously handling worker checkpoints. Review inbox to observe or override."
        : "Worker is running. The manager will handle questions automatically.";
  }

  useEffect(() => {
    if (!selectedPortfolio && mainView !== "workspace") {
      setMainView("workspace");
      return;
    }
    if (mainView === "activity" && !selectedProject) {
      setMainView(openCheckpointCount > 0 ? "inbox" : "workspace");
    }
  }, [mainView, openCheckpointCount, selectedPortfolio, selectedProject]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const bootstrap = await fetchBootstrap();
        if (cancelled) return;
        startTransition(() => {
          setHealth(bootstrap.health);
          setPortfolios(bootstrap.portfolios);
          setSelectedPortfolioId((currentId) =>
            choosePortfolioId(bootstrap.portfolios, currentId),
          );
          setLoading(false);
        });
      } catch (error) {
        if (cancelled) return;
        setFlash({
          tone: "danger",
          message:
            error instanceof Error ? error.message : "Failed to load portfolios.",
        });
        setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedPortfolioId) {
      startTransition(() => {
        setProjects([]);
        setSelectedProjectId(null);
        setSessionsByProject({});
        setManagerSession(null);
        setInbox([]);
        setEvents([]);
        setLoading(false);
      });
      return;
    }

    const portfolio =
      portfolios.find((candidate) => candidate.id === selectedPortfolioId) ?? null;
    if (!portfolio) {
      return;
    }
    const activePortfolio = portfolio;

    let cancelled = false;
    setRefreshing(true);

    async function loadBoard() {
      try {
        const board = await fetchPortfolioBoard(activePortfolio, selectedProjectId);
        if (cancelled) return;
        startTransition(() => {
          setProjects(board.projects);
          setSessionsByProject(board.sessionsByProject);
          setManagerSession(board.managerSession);
          setInbox(board.inbox);
          setSelectedProjectId(board.suggestedProjectId);
          setEvents(board.events);
          setRefreshing(false);
          setLoading(false);
        });
      } catch (error) {
        if (cancelled) return;
        setRefreshing(false);
        setFlash({
          tone: "danger",
          message:
            error instanceof Error ? error.message : "Failed to load portfolio board.",
        });
      }
    }

    void loadBoard();
    return () => {
      cancelled = true;
    };
  }, [portfolios, selectedPortfolioId, selectedProjectId, startTransition]);

  useEffect(() => {
    if (!selectedPortfolioId) {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        const bootstrap = await fetchBootstrap();
        let nextEvents: EventEnvelope[] = [];
        if (selectedProjectId) {
          nextEvents = await listProjectEvents(selectedProjectId, 50);
        }
        startTransition(() => {
          setHealth(bootstrap.health);
          setPortfolios(bootstrap.portfolios);
          setEvents(nextEvents);
        });
      } catch {}
    }, 5000);

    return () => {
      window.clearInterval(timer);
    };
  }, [selectedPortfolioId, selectedProjectId, startTransition]);

  // Load deliverable files when tab is active
  useEffect(() => {
    if (mainView !== "deliverables" || !selectedProjectId) {
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [files, preview] = await Promise.all([
          listProjectFiles(selectedProjectId, deliverablePath),
          deliverablePath === "" ? getPreviewInfo(selectedProjectId) : Promise.resolve(null),
        ]);
        if (!cancelled) {
          setDeliverableFiles(files);
          if (preview) setPreviewInfo(preview);
        }
      } catch {
        if (!cancelled) setDeliverableFiles([]);
      }
    })();
    return () => { cancelled = true; };
  }, [mainView, selectedProjectId, deliverablePath]);

  async function handleOpenFile(filePath: string) {
    if (!selectedProjectId) return;
    setLoadingFile(true);
    try {
      const content = await getProjectFileContent(selectedProjectId, filePath);
      setOpenFile(content);
    } catch {
      setOpenFile(null);
    } finally {
      setLoadingFile(false);
    }
  }

  function handleNavigateDir(dirPath: string) {
    setDeliverablePath(dirPath);
    setOpenFile(null);
  }

  async function handlePushGitHub() {
    if (!selectedProjectId) return;
    setPushingGh(true);
    setGhPushResult(null);
    try {
      const result = await pushToGitHub(selectedProjectId);
      setGhPushResult(result);
    } catch (e) {
      setGhPushResult({ success: false, repo_url: null, error: String(e) });
    } finally {
      setPushingGh(false);
    }
  }

  function handleNavigateUp() {
    const parts = deliverablePath.split("/").filter(Boolean);
    parts.pop();
    setDeliverablePath(parts.join("/"));
    setOpenFile(null);
  }

  async function refreshAfterAction(
    preferredPortfolioId: string | null,
    preferredProjectId: string | null,
  ) {
    const bootstrap = await fetchBootstrap();
    const nextPortfolioId = choosePortfolioId(bootstrap.portfolios, preferredPortfolioId);
    startTransition(() => {
      setHealth(bootstrap.health);
      setPortfolios(bootstrap.portfolios);
      setSelectedPortfolioId(nextPortfolioId);
      setSelectedProjectId(preferredProjectId);
    });
  }

  async function runAction(
    label: string,
    action: () => Promise<void>,
  ) {
    setPendingAction(label);
    setFlash(null);
    try {
      await action();
      setFlash({ tone: "success", message: label });
    } catch (error) {
      setFlash({
        tone: "danger",
        message: error instanceof Error ? error.message : "The action failed.",
      });
    } finally {
      setPendingAction(null);
    }
  }

  async function handleCreatePortfolio(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = portfolioForm.name.trim();
    const goal = portfolioForm.goal.trim();
    if (!name) return;

    await runAction("Portfolio created", async () => {
      const portfolio = await createPortfolio({ name, goal });
      setPortfolioForm({ name: "", goal: "" });
      setShowNewPortfolio(false);
      await refreshAfterAction(portfolio.id, null);
    });
  }

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = projectForm.name.trim();
    const repoPath = projectForm.repoPath.trim();
    const objective = projectForm.objective.trim();
    const createRepo = projectForm.createRepo;
    if (!name) {
      setFlash({ tone: "danger", message: "Project name is required." });
      return;
    }
    if (!createRepo && !repoPath) {
      setFlash({ tone: "danger", message: "Repo path is required when auto-create is off." });
      return;
    }

    await runAction("Project created", async () => {
      const portfolio =
        selectedPortfolio ??
        (await createPortfolio({
          name: "Default Portfolio",
          goal: "Manage the active project sessions in this workspace.",
        }));
      const project = await createProject({
        portfolioId: portfolio.id,
        name,
        repoPath: createRepo ? null : repoPath,
        createRepo,
        defaultBranch: projectForm.defaultBranch.trim() || null,
        objective,
      });
      setProjectForm({
        name: "",
        repoPath: "",
        createRepo: true,
        defaultBranch: "main",
        objective: "",
      });
      setShowNewProject(false);
      await refreshAfterAction(portfolio.id, project.id);
    });
  }

  async function handleStartManager() {
    if (!selectedPortfolio) return;
    await runAction("Manager session started", async () => {
      await startPortfolioManager({
        portfolioId: selectedPortfolio.id,
        runtimePreference: "auto",
        allowSimulationFallback: true,
        initialMessage:
          selectedPortfolio.goal ||
          `Supervise the ${selectedPortfolio.name} portfolio and keep each project moving.`,
      });
      await refreshAfterAction(selectedPortfolio.id, selectedProjectId);
    });
  }

  async function handleStartProject(project: Project) {
    await runAction("Project session started", async () => {
      await startProjectExecution({
        projectId: project.id,
        runtimePreference: "auto",
        allowSimulationFallback: true,
        initialMessage:
          project.objective ||
          `Work independently on ${project.name} and report progress back to the manager.`,
      });
      await refreshAfterAction(project.portfolio_id, project.id);
    });
  }

  async function handleSendInstruction() {
    if (!selectedProject) return;
    const message = instructionDraft.trim();
    if (!message) return;

    await runAction("Instruction sent", async () => {
      await sendProjectManagerInstruction({
        projectId: selectedProject.id,
        message,
        metadata: { source: "portfolio_dashboard" },
      });
      setInstructionDraft("");
      await refreshAfterAction(selectedProject.portfolio_id, selectedProject.id);
    });
  }

  async function handleCheckpointAction(
    checkpoint: ProjectCheckpoint,
    action: ProjectCheckpointAction,
  ) {
    const message = checkpointDrafts[checkpoint.id]?.trim() ?? "";
    if ((action === "answer" || action === "request_changes") && !message) {
      setFlash({
        tone: "danger",
        message: `${checkpointActionLabel(action)} requires a message.`,
      });
      return;
    }

    await runAction(`${checkpointActionLabel(action)} applied`, async () => {
      await respondToPortfolioCheckpoint({
        portfolioId: checkpoint.portfolio_id,
        checkpointId: checkpoint.id,
        action,
        message: message || null,
        details: { source: "portfolio_dashboard" },
      });
      setCheckpointDrafts((current) => ({ ...current, [checkpoint.id]: "" }));
      await refreshAfterAction(checkpoint.portfolio_id, checkpoint.project_id);
    });
  }

  const connectionStatus =
    health.status === "ok" ? "ok" : health.status === "loading" ? "connecting" : "error";

  /* ═══════════════════════════════════════════
     Render
     ═══════════════════════════════════════════ */
  return (
    <div className="console">
      {/* ─── Top bar ─── */}
      <header className="topbar">
        <div className="topbar-left">
          <span className="con-dot" data-status={connectionStatus} />
          <span className="brand">POULPE<span>console</span></span>
          <div className="topbar-divider" />
          {portfolios.map((portfolio) => (
            <button
              key={portfolio.id}
              className={`pf-chip ${portfolio.id === selectedPortfolioId ? "is-active" : ""}`}
              type="button"
              onClick={() => setSelectedPortfolioId(portfolio.id)}
            >
              {portfolio.name}
            </button>
          ))}
          <button
            className="pf-chip is-add"
            type="button"
            onClick={() => setShowNewPortfolio(!showNewPortfolio)}
          >
            +
          </button>
          {refreshing ? <span className="sync-badge">syncing</span> : null}
        </div>
        <div className="topbar-right">
          <ThemeToggle />
        </div>
      </header>

      {/* Inline portfolio create form (dropdown under topbar) */}
      {showNewPortfolio ? (
        <div className="pf-form-inline">
          <form
            style={{ display: "flex", flexDirection: "column", gap: "8px" }}
            onSubmit={handleCreatePortfolio}
          >
            <div className="field">
              <span className="field-label">Portfolio name</span>
              <input
                className="field-input"
                value={portfolioForm.name}
                onChange={(e) =>
                  setPortfolioForm((c) => ({ ...c, name: e.target.value }))
                }
                placeholder="Platform modernization"
              />
            </div>
            <div className="field">
              <span className="field-label">Goal (optional)</span>
              <textarea
                className="field-textarea"
                value={portfolioForm.goal}
                onChange={(e) =>
                  setPortfolioForm((c) => ({ ...c, goal: e.target.value }))
                }
                rows={2}
                placeholder="Coordinate and deliver the active projects."
              />
            </div>
            <div style={{ display: "flex", gap: "6px" }}>
              <button className="btn-accent" disabled={pendingAction !== null} type="submit">
                Create portfolio
              </button>
              <button
                className="btn-ghost"
                type="button"
                onClick={() => setShowNewPortfolio(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}

      {/* ─── Flash ─── */}
      {flash ? (
        <div className={`flash-bar is-${flash.tone}`}>{flash.message}</div>
      ) : null}

      {/* ─── Body ─── */}
      {loading ? (
        <div className="loading-state">
          <div className="spinner" />
          <span>Connecting to backend&hellip;</span>
        </div>
      ) : !selectedPortfolio ? (
        /* ─── Onboarding (no portfolio) ─── */
        <div className="onboard">
          <div className="onboard-card">
            <h1>Create your first portfolio</h1>
            <p>
              A portfolio groups independent coding-agent projects under one
              manager session. Create one to get started.
            </p>
            <form
              style={{ display: "flex", flexDirection: "column", gap: "10px" }}
              onSubmit={handleCreatePortfolio}
            >
              <div className="field">
                <span className="field-label">Name</span>
                <input
                  className="field-input"
                  value={portfolioForm.name}
                  onChange={(e) =>
                    setPortfolioForm((c) => ({ ...c, name: e.target.value }))
                  }
                  placeholder="Platform modernization"
                />
              </div>
              <div className="field">
                <span className="field-label">Goal</span>
                <textarea
                  className="field-textarea"
                  value={portfolioForm.goal}
                  onChange={(e) =>
                    setPortfolioForm((c) => ({ ...c, goal: e.target.value }))
                  }
                  rows={3}
                  placeholder="Coordinate the active project sessions and bring them to completion."
                />
              </div>
              <button
                className="btn-accent"
                disabled={pendingAction !== null}
                type="submit"
              >
                Create portfolio
              </button>
            </form>
          </div>
        </div>
      ) : (
        /* ─── Console body ─── */
        <div className="console-body">
          {/* ─── Sidebar ─── */}
          <aside className="sidebar">
            {/* Manager section */}
            <div className="sb-section">
              <div className="sb-header">
                <span className="sb-label">Manager</span>
                {managerSession ? (
                  <StatusPill tone={statusToneFor(managerSession.status)} compact>
                    {managerSession.status}
                  </StatusPill>
                ) : (
                  <span className="sb-hint">not started</span>
                )}
              </div>
              {managerSession ? (
                <div className="manager-row">
                  <span className="mono">{shortId(managerSession.id)}</span>
                  <button
                    className="btn-ghost"
                    disabled={pendingAction !== null}
                    onClick={() => void handleStartManager()}
                    type="button"
                  >
                    Restart
                  </button>
                </div>
              ) : (
                <button
                  className="btn-accent"
                  disabled={pendingAction !== null}
                  onClick={() => void handleStartManager()}
                  type="button"
                  style={{ width: "100%" }}
                >
                  Start manager
                </button>
              )}
            </div>

            {/* Projects section */}
            <div className="sb-section sb-section-grow">
              <div className="sb-header">
                <span className="sb-label">Projects</span>
                <span className="sb-count">{projects.length}</span>
              </div>
              <div className="sb-scroll">
                {(() => {
                  // Group projects: parents (or standalone) first, then children underneath
                  const parentProjects = projects.filter(
                    (p) => !p.parent_project_id,
                  );
                  const childrenByParent: Record<string, Project[]> = {};
                  for (const p of projects) {
                    if (p.parent_project_id) {
                      if (!childrenByParent[p.parent_project_id]) {
                        childrenByParent[p.parent_project_id] = [];
                      }
                      childrenByParent[p.parent_project_id].push(p);
                    }
                  }

                  const renderProjectItem = (
                    project: Project,
                    isChild: boolean,
                  ) => {
                    const worker = primaryWorkerSession(
                      project,
                      sessionsByProject[project.id] ?? [],
                    );
                    const selected = project.id === selectedProjectId;
                    const children = childrenByParent[project.id];
                    const isParent = children && children.length > 0;
                    const isDecomposedParent = Boolean(
                      (project.metadata as Record<string, unknown>)?.is_parent,
                    );

                    return (
                      <div key={project.id}>
                        <button
                          className={`pj-item ${selected ? "is-active" : ""} ${isChild ? "pj-child" : ""}`}
                          type="button"
                          onClick={() =>
                            setSelectedProjectId(project.id)
                          }
                        >
                          <div className="pj-row">
                            <span className="pj-name">
                              {isDecomposedParent && isParent
                                ? project.name
                                : isChild
                                  ? project.name
                                  : project.name}
                            </span>
                            <span
                              className="pj-dot"
                              data-status={
                                worker?.status ?? "idle"
                              }
                            />
                          </div>
                          <span className="pj-detail">
                            {isParent
                              ? `${children.length} sub-projects`
                              : `${worker?.status ?? "idle"} \u00B7 ${compactPath(project.repo_path)}`}
                          </span>
                        </button>
                        {isParent
                          ? children.map((child) =>
                              renderProjectItem(child, true),
                            )
                          : null}
                      </div>
                    );
                  };

                  return parentProjects.map((project) =>
                    renderProjectItem(project, false),
                  );
                })()}
                {projects.length === 0 ? (
                  <div className="sb-empty">
                    No projects yet. Add one below.
                  </div>
                ) : null}
              </div>
            </div>

            {/* New project section */}
            <div className="sb-section">
              <button
                className="sb-toggle"
                type="button"
                onClick={() => setShowNewProject(!showNewProject)}
              >
                {showNewProject ? "Cancel" : "+ New project"}
              </button>
              {showNewProject ? (
                <form className="sb-form" onSubmit={handleCreateProject}>
                  <div className="field">
                    <span className="field-label">Name</span>
                    <input
                      className="field-input"
                      value={projectForm.name}
                      onChange={(e) =>
                        setProjectForm((c) => ({ ...c, name: e.target.value }))
                      }
                      placeholder="Auth API hardening"
                    />
                  </div>
                  <label className="field-check">
                    <input
                      checked={projectForm.createRepo}
                      onChange={(e) =>
                        setProjectForm((c) => ({
                          ...c,
                          createRepo: e.target.checked,
                        }))
                      }
                      type="checkbox"
                    />
                    <span>Auto-create repo</span>
                  </label>
                  {!projectForm.createRepo ? (
                    <div className="field">
                      <span className="field-label">Repo path</span>
                      <input
                        className="field-input"
                        value={projectForm.repoPath}
                        onChange={(e) =>
                          setProjectForm((c) => ({
                            ...c,
                            repoPath: e.target.value,
                          }))
                        }
                        placeholder="/path/to/repo"
                      />
                    </div>
                  ) : null}
                  <div className="field">
                    <span className="field-label">Objective (optional)</span>
                    <textarea
                      className="field-textarea"
                      value={projectForm.objective}
                      onChange={(e) =>
                        setProjectForm((c) => ({
                          ...c,
                          objective: e.target.value,
                        }))
                      }
                      rows={2}
                      placeholder="What should the worker do?"
                    />
                  </div>
                  <button
                    className="btn-accent"
                    disabled={pendingAction !== null}
                    type="submit"
                    style={{ width: "100%" }}
                  >
                    Add project
                  </button>
                </form>
              ) : null}
            </div>
          </aside>

          {/* ─── Main area ─── */}
          <main className="main">
            {/* Status strip */}
            <div className="status-strip">
              <div className="strip-left">
                <h1 className="strip-title">{selectedPortfolio.name}</h1>
                <p className="strip-goal">
                  {selectedPortfolio.goal || "No portfolio goal set."}
                </p>
              </div>
              <div className="strip-stats">
                <div className="stat-box">
                  <span className="stat-val">{portfolioProjectCount}</span>
                  <span className="stat-label">Projects</span>
                </div>
                <div className="stat-box">
                  <span
                    className="stat-val"
                    data-accent={activeWorkerCount > 0 ? "true" : undefined}
                  >
                    {activeWorkerCount}
                  </span>
                  <span className="stat-label">Active</span>
                </div>
                <div className="stat-box">
                  <span
                    className="stat-val"
                    data-warn={openCheckpointCount > 0 ? "true" : undefined}
                  >
                    {openCheckpointCount}
                  </span>
                  <span className="stat-label">Inbox</span>
                </div>
              </div>
              <div className="strip-actions">
                {selectedProject && !selectedWorker ? (
                  <button
                    className="btn-outline"
                    disabled={pendingAction !== null}
                    onClick={() => void handleStartProject(selectedProject)}
                    type="button"
                  >
                    Start project
                  </button>
                ) : null}
                {openCheckpointCount > 0 ? (
                  <button
                    className="btn-outline"
                    onClick={() => setMainView("inbox")}
                    type="button"
                  >
                    View inbox
                  </button>
                ) : null}
              </div>
            </div>

            {/* Prompt bar */}
            <div className="prompt-bar">
              <span className="prompt-dot" />
              <span className="prompt-text">
                <strong>{nextActionTitle}</strong> &mdash; {nextActionDetail}
              </span>
            </div>

            {/* Tab bar */}
            <div className="tab-bar">
              <button
                className="tab"
                data-active={mainView === "workspace"}
                onClick={() => setMainView("workspace")}
                type="button"
              >
                Workspace
              </button>
              <button
                className="tab"
                data-active={mainView === "inbox"}
                onClick={() => setMainView("inbox")}
                type="button"
              >
                Inbox
                {openCheckpointCount > 0 ? (
                  <span className="tab-badge" data-warn="true">
                    {openCheckpointCount}
                  </span>
                ) : null}
              </button>
              <button
                className="tab"
                data-active={mainView === "deliverables"}
                onClick={() => {
                  setMainView("deliverables");
                  setDeliverablePath("");
                  setOpenFile(null);
                }}
                type="button"
              >
                Deliverables
              </button>
              <button
                className="tab"
                data-active={mainView === "activity"}
                onClick={() => setMainView("activity")}
                type="button"
              >
                Activity
              </button>
            </div>

            {/* ─── View content ─── */}
            <div className="view-content">
              {/* === Workspace === */}
              {mainView === "workspace" ? (
                <div className="ws-layout">
                  {/* Selected project detail */}
                  {selectedProject ? (
                    <div className="detail-panel">
                      <div className="detail-header">
                        <h2>{selectedProject.name}</h2>
                        <StatusPill
                          tone={statusToneFor(
                            selectedWorker?.status ?? "idle",
                          )}
                        >
                          {selectedWorker?.status ?? "idle"}
                        </StatusPill>
                      </div>

                      <div className="detail-grid">
                        <div className="detail-item">
                          <span className="detail-key">Objective</span>
                          <span className="detail-val">
                            {selectedProject.objective || "No objective set"}
                          </span>
                        </div>
                        <div className="detail-item">
                          <span className="detail-key">Repo</span>
                          <span className="detail-val mono">
                            {selectedProject.repo_path}
                          </span>
                        </div>
                        <div className="detail-item">
                          <span className="detail-key">Worker</span>
                          <span className="detail-val mono">
                            {selectedWorker
                              ? `${shortId(selectedWorker.id)} · ${selectedWorker.runtime.resolved_provider}`
                              : "Not started"}
                          </span>
                        </div>
                        <div className="detail-item">
                          <span className="detail-key">Checkpoints</span>
                          <span className="detail-val">
                            {selectedProjectCheckpointCount || "None open"}
                          </span>
                        </div>
                      </div>

                      {/* Live progress feed */}
                      {(() => {
                        const progressEvents = events.filter(
                          (e) =>
                            (e.event_type === "session.progress" ||
                              e.event_type === "session.start" ||
                              e.event_type === "session.tests_run" ||
                              e.event_type === "session.complete") &&
                            e.payload?.summary,
                        );
                        const isRunning =
                          selectedWorker?.status === "running" ||
                          selectedWorker?.status === "starting";
                        if (progressEvents.length === 0 && !isRunning)
                          return null;

                        // Gather file changes from progress events
                        const allFiles: string[] = [];
                        for (const e of progressEvents) {
                          const files = e.payload?.files;
                          if (Array.isArray(files)) {
                            for (const f of files) {
                              if (typeof f === "string" && !allFiles.includes(f))
                                allFiles.push(f);
                            }
                          }
                        }

                        // Estimate progress based on elapsed time and event pattern
                        const startedAt = selectedWorker?.started_at;
                        const elapsedMs = startedAt
                          ? Date.now() - new Date(startedAt).getTime()
                          : 0;
                        const elapsedMin = Math.floor(elapsedMs / 60_000);
                        const hasTests = progressEvents.some(
                          (e) => e.event_type === "session.tests_run",
                        );
                        const hasComplete = progressEvents.some(
                          (e) => e.event_type === "session.complete",
                        );

                        // Use explicit progress % from worker if available
                        const lastExplicitPct = [...progressEvents]
                          .reverse()
                          .find(
                            (e) =>
                              typeof e.payload?.progress === "number" &&
                              e.payload.progress > 0,
                          )?.payload?.progress as number | undefined;

                        // Heuristic progress: files created = work done
                        let estimatedPct: number;
                        if (lastExplicitPct && lastExplicitPct > 0) {
                          estimatedPct = Math.min(99, lastExplicitPct);
                        } else {
                          estimatedPct = Math.min(
                            95,
                            Math.round(
                              (allFiles.length / 15) * 70 +
                                (hasTests ? 15 : 0) +
                                (hasComplete ? 100 : 0),
                            ),
                          );
                        }
                        if (isRunning && estimatedPct < 5 && elapsedMs > 10_000)
                          estimatedPct = 5;
                        if (hasComplete) estimatedPct = 100;

                        // Get latest next_step for display
                        const latestNextStep = [...progressEvents]
                          .reverse()
                          .find((e) => e.payload?.next_step)?.payload
                          ?.next_step as string | undefined;

                        // Time estimate: average project takes ~8-15 min
                        // If we have progress, extrapolate
                        let timeEstimate = "";
                        if (isRunning && estimatedPct > 5 && estimatedPct < 95) {
                          const totalEstMs =
                            elapsedMs / (estimatedPct / 100);
                          const remainMs = totalEstMs - elapsedMs;
                          const remainMin = Math.ceil(remainMs / 60_000);
                          if (remainMin > 0 && remainMin < 60) {
                            timeEstimate = `~${remainMin}m remaining`;
                          }
                        } else if (isRunning && estimatedPct <= 5) {
                          timeEstimate = "estimating...";
                        }

                        return (
                          <div className="progress-feed">
                            <div className="progress-feed-header">
                              <span className="progress-feed-label">
                                {isRunning
                                  ? "Live progress"
                                  : hasComplete
                                    ? "Completed"
                                    : "Last session"}
                              </span>
                              {isRunning ? (
                                <span className="progress-feed-dot" />
                              ) : null}
                              {timeEstimate ? (
                                <span className="progress-feed-eta">
                                  {timeEstimate}
                                </span>
                              ) : null}
                            </div>

                            {/* Progress bar */}
                            {isRunning || hasComplete ? (
                              <div className="progress-bar-track">
                                <div
                                  className="progress-bar-fill"
                                  data-complete={hasComplete ? "true" : undefined}
                                  style={{
                                    width: `${estimatedPct}%`,
                                  }}
                                />
                                <span className="progress-bar-label">
                                  {estimatedPct}%
                                  {allFiles.length > 0
                                    ? ` · ${allFiles.length} files`
                                    : ""}
                                  {elapsedMin > 0
                                    ? ` · ${elapsedMin}m elapsed`
                                    : ""}
                                </span>
                              </div>
                            ) : null}

                            {/* Next step indicator */}
                            {latestNextStep && isRunning ? (
                              <div className="progress-next-step">
                                Next: {latestNextStep}
                              </div>
                            ) : null}

                            {/* Files being created */}
                            {allFiles.length > 0 ? (
                              <div className="progress-files">
                                {allFiles.slice(-8).map((f) => (
                                  <span key={f} className="progress-file-tag">
                                    {f.split("/").pop()}
                                  </span>
                                ))}
                                {allFiles.length > 8 ? (
                                  <span className="progress-file-tag progress-file-more">
                                    +{allFiles.length - 8} more
                                  </span>
                                ) : null}
                              </div>
                            ) : null}

                            <div className="progress-feed-list">
                              {progressEvents
                                .slice(-8)
                                .reverse()
                                .map((e) => (
                                  <div
                                    key={e.id}
                                    className="progress-feed-item"
                                  >
                                    <span
                                      className="progress-feed-icon"
                                      data-type={e.event_type.replace(
                                        "session.",
                                        "",
                                      )}
                                    >
                                      {e.event_type === "session.tests_run"
                                        ? e.payload?.status === "passed"
                                          ? "\u2713"
                                          : "\u2717"
                                        : e.event_type === "session.complete"
                                          ? "\u2605"
                                          : e.event_type === "session.start"
                                            ? "\u25B6"
                                            : "\u2022"}
                                    </span>
                                    <span className="progress-feed-time">
                                      {friendlyTime(e.occurred_at)}
                                    </span>
                                    <span className="progress-feed-msg">
                                      {String(e.payload?.summary ?? "")}
                                    </span>
                                  </div>
                                ))}
                              {progressEvents.length === 0 && isRunning ? (
                                <div className="progress-feed-item">
                                  <span className="progress-feed-msg">
                                    Worker is starting up...
                                  </span>
                                </div>
                              ) : null}
                            </div>
                          </div>
                        );
                      })()}

                      {selectedProject.completion_summary ? (
                        <div className="completion-note">
                          {selectedProject.completion_summary}
                        </div>
                      ) : null}

                      {/* Continue / instruct box */}
                      <div className="instruction-box">
                        <textarea
                          className="instruction-input"
                          value={instructionDraft}
                          onChange={(e) =>
                            setInstructionDraft(e.target.value)
                          }
                          placeholder={
                            selectedProject.completion_summary
                              ? "Continue project — describe the next task..."
                              : "Send instruction to worker..."
                          }
                          rows={2}
                        />
                        {selectedProject.completion_summary ? (
                          <button
                            className="btn-accent"
                            disabled={
                              pendingAction !== null ||
                              !instructionDraft.trim()
                            }
                            onClick={() => {
                              const msg = instructionDraft.trim();
                              if (!msg) return;
                              void (async () => {
                                await runAction(
                                  "Continued project",
                                  async () => {
                                    await startProjectExecution({
                                      projectId: selectedProject.id,
                                      runtimePreference: "auto",
                                      allowSimulationFallback: true,
                                      initialMessage: msg,
                                    });
                                    setInstructionDraft("");
                                    await refreshAfterAction(
                                      selectedProject.portfolio_id,
                                      selectedProject.id,
                                    );
                                  },
                                );
                              })();
                            }}
                            type="button"
                          >
                            Continue project
                          </button>
                        ) : (
                          <button
                            className="btn-sm"
                            disabled={pendingAction !== null}
                            onClick={() => void handleSendInstruction()}
                            type="button"
                          >
                            Send
                          </button>
                        )}
                      </div>

                      <div className="detail-actions">
                        <button
                          className="btn-accent"
                          disabled={pendingAction !== null}
                          onClick={() =>
                            void handleStartProject(selectedProject)
                          }
                          type="button"
                        >
                          {selectedWorker ? "Restart" : "Start"} project
                        </button>
                        <button
                          className="btn-ghost"
                          onClick={() => setMainView("activity")}
                          type="button"
                        >
                          View activity
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="empty-panel">
                      Select a project from the sidebar to view details and
                      send instructions.
                    </div>
                  )}

                  {/* Project overview cards (right side) */}
                  <div className="project-overview">
                    <div className="overview-header">
                      <span className="overview-label">
                        All projects ({projects.length})
                      </span>
                    </div>
                    {projects.map((project) => {
                      const worker = primaryWorkerSession(
                        project,
                        sessionsByProject[project.id] ?? [],
                      );
                      const openForProject = inbox.filter(
                        (c) => c.project_id === project.id,
                      ).length;
                      const selected = project.id === selectedProjectId;

                      return (
                        <div
                          key={project.id}
                          className={`pj-card ${selected ? "is-active" : ""}`}
                          onClick={() => setSelectedProjectId(project.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter")
                              setSelectedProjectId(project.id);
                          }}
                          tabIndex={0}
                          role="button"
                        >
                          <div className="pj-card-head">
                            <span className="pj-card-title">
                              {project.name}
                            </span>
                            <StatusPill
                              tone={statusToneFor(
                                worker?.status ?? project.status,
                              )}
                              compact
                            >
                              {worker?.status ?? "idle"}
                            </StatusPill>
                          </div>
                          {project.objective ? (
                            <p className="pj-card-obj">
                              {project.objective}
                            </p>
                          ) : null}
                          <div className="pj-card-meta">
                            <span className="meta-chip">
                              {compactPath(project.repo_path)}
                            </span>
                            <span className="meta-chip">
                              {project.default_branch}
                            </span>
                            {openForProject > 0 ? (
                              <span className="meta-chip">
                                {openForProject} checkpoint(s)
                              </span>
                            ) : null}
                            {worker ? (
                              <span className="meta-chip">
                                {worker.runtime.resolved_provider}
                              </span>
                            ) : null}
                          </div>
                          <div
                            className="detail-actions"
                            style={{ marginTop: "4px" }}
                          >
                            <button
                              className="btn-sm"
                              disabled={pendingAction !== null}
                              onClick={(e) => {
                                e.stopPropagation();
                                void handleStartProject(project);
                              }}
                              type="button"
                            >
                              {worker ? "Restart" : "Start"}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                    {projects.length === 0 ? (
                      <div className="sb-empty">
                        Add a project via the sidebar to create its worker
                        lane.
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {/* === Inbox === */}
              {mainView === "inbox" ? (
                <div>
                  <div className="inbox-header">
                    <span className="inbox-title">
                      Manager inbox &middot; {openCheckpointCount} open
                    </span>
                  </div>
                  <div className="inbox-list">
                    {inbox.map((checkpoint) => {
                      const details = asRecord(checkpoint.details);
                      const reviewContext = asRecord(details.review_context);
                      const diffDetails = asRecord(reviewContext.diff);
                      const checks = asArray(reviewContext.checks);
                      const draft = checkpointDrafts[checkpoint.id] ?? "";

                      return (
                        <article
                          className="ckpt-card"
                          data-kind={checkpoint.kind}
                          key={checkpoint.id}
                        >
                          <div className="ckpt-header">
                            <div>
                              <p className="ckpt-project">
                                {checkpoint.project_name}
                              </p>
                              <h3>{checkpoint.summary}</h3>
                            </div>
                            <div className="ckpt-meta">
                              <StatusPill
                                tone={statusToneFor(checkpoint.kind)}
                                compact
                              >
                                {checkpoint.kind}
                              </StatusPill>
                              <span className="ckpt-time">
                                {friendlyTime(checkpoint.source_occurred_at)}
                              </span>
                            </div>
                          </div>

                          {asString(reviewContext.error) ? (
                            <p className="ckpt-warning">
                              {asString(reviewContext.error)}
                            </p>
                          ) : null}

                          {checkpoint.kind === "completion" ? (
                            <div className="review-ctx">
                              <div className="review-stat-row">
                                <span className="review-stat">
                                  {asString(diffDetails.summary) ??
                                    "No diff summary"}
                                </span>
                                <span className="review-stat">
                                  {asArray(diffDetails.changed_files).length}{" "}
                                  file(s)
                                </span>
                                <span className="review-stat">
                                  {checks.length} check(s)
                                </span>
                              </div>
                              {asArray(diffDetails.changed_files).length >
                              0 ? (
                                <div className="file-chips">
                                  {asArray(diffDetails.changed_files).map(
                                    (item) => (
                                      <span
                                        className="file-chip"
                                        key={String(item)}
                                      >
                                        {String(item)}
                                      </span>
                                    ),
                                  )}
                                </div>
                              ) : null}
                              {asString(diffDetails.diff_preview) ? (
                                <pre className="diff-block">
                                  {String(diffDetails.diff_preview)}
                                </pre>
                              ) : null}
                              {checks.length > 0 ? (
                                <div className="check-list">
                                  {checks.map((check, index) => {
                                    const checkRecord = asRecord(check);
                                    return (
                                      <div
                                        className="check-item"
                                        key={`${checkpoint.id}-${index}`}
                                      >
                                        <StatusPill
                                          tone={statusToneFor(
                                            asString(checkRecord.status) ??
                                              "info",
                                          )}
                                          compact
                                        >
                                          {asString(checkRecord.kind) ??
                                            "check"}
                                        </StatusPill>
                                        <span className="mono">
                                          {asString(checkRecord.command) ??
                                            "—"}
                                        </span>
                                        <span>
                                          {asString(checkRecord.status) ??
                                            "unknown"}
                                        </span>
                                      </div>
                                    );
                                  })}
                                </div>
                              ) : null}
                            </div>
                          ) : null}

                          <div className="ckpt-response">
                            <textarea
                              className="ckpt-textarea"
                              rows={2}
                              value={draft}
                              onChange={(e) =>
                                setCheckpointDrafts((c) => ({
                                  ...c,
                                  [checkpoint.id]: e.target.value,
                                }))
                              }
                              placeholder={
                                checkpoint.kind === "completion"
                                  ? "Request changes or leave blank to approve."
                                  : "Answer the project so it can continue."
                              }
                            />
                          </div>

                          <div className="ckpt-actions">
                            {checkpoint.kind === "completion" ? (
                              <>
                                <button
                                  className="btn-accent"
                                  disabled={pendingAction !== null}
                                  onClick={() =>
                                    void handleCheckpointAction(
                                      checkpoint,
                                      "approve",
                                    )
                                  }
                                  type="button"
                                >
                                  Approve
                                </button>
                                <button
                                  className="btn-outline"
                                  disabled={pendingAction !== null}
                                  onClick={() =>
                                    void handleCheckpointAction(
                                      checkpoint,
                                      "request_changes",
                                    )
                                  }
                                  type="button"
                                >
                                  Request changes
                                </button>
                              </>
                            ) : (
                              <button
                                className="btn-accent"
                                disabled={pendingAction !== null}
                                onClick={() =>
                                  void handleCheckpointAction(
                                    checkpoint,
                                    "answer",
                                  )
                                }
                                type="button"
                              >
                                Answer
                              </button>
                            )}
                            <button
                              className="btn-ghost"
                              disabled={pendingAction !== null}
                              onClick={() =>
                                void handleCheckpointAction(
                                  checkpoint,
                                  "dismiss",
                                )
                              }
                              type="button"
                            >
                              Dismiss
                            </button>
                          </div>
                        </article>
                      );
                    })}

                    {inbox.length === 0 ? (
                      <div className="empty-panel">
                        No open inbox items. Start or continue projects and
                        checkpoints will appear here.
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {/* === Deliverables === */}
              {mainView === "deliverables" ? (
                <div className="deliverables-view">
                  {!selectedProject ? (
                    <div className="empty-panel">
                      Select a project to view its deliverables.
                    </div>
                  ) : showPreview && previewInfo?.preview_url ? (
                    <div className="preview-panel">
                      <div className="preview-header">
                        <button
                          className="btn-ghost"
                          onClick={() => setShowPreview(false)}
                          type="button"
                        >
                          &larr; Files
                        </button>
                        <span className="preview-label">
                          Live preview &mdash;{" "}
                          <span className="mono">
                            {previewInfo.entry_file}
                          </span>
                        </span>
                        <a
                          className="btn-ghost"
                          href={`http://localhost:8001${previewInfo.preview_url}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          Open in new tab
                        </a>
                      </div>
                      <iframe
                        className="preview-iframe"
                        src={`http://localhost:8001${previewInfo.preview_url}`}
                        title="Project preview"
                        sandbox="allow-scripts allow-same-origin"
                      />
                    </div>
                  ) : openFile ? (
                    <div className="file-viewer">
                      <div className="file-viewer-header">
                        <button
                          className="btn-ghost"
                          onClick={() => setOpenFile(null)}
                          type="button"
                        >
                          &larr; Back
                        </button>
                        <span className="file-viewer-path mono">
                          {openFile.path}
                        </span>
                        <span className="file-viewer-meta">
                          {(openFile.size / 1024).toFixed(1)} KB
                        </span>
                      </div>
                      <pre className="file-viewer-content">
                        {openFile.content}
                      </pre>
                    </div>
                  ) : (
                    <div className="file-browser">
                      {/* Action bar */}
                      <div className="deliverables-actions">
                        {previewInfo?.available ? (
                          <button
                            className="btn-accent"
                            onClick={() => setShowPreview(true)}
                            type="button"
                          >
                            Preview{" "}
                            {previewInfo.kind === "html"
                              ? "app"
                              : "document"}
                          </button>
                        ) : null}
                        <button
                          className="btn-outline"
                          disabled={pushingGh}
                          onClick={() => void handlePushGitHub()}
                          type="button"
                        >
                          {pushingGh
                            ? "Pushing..."
                            : "Push to GitHub"}
                        </button>
                        {ghPushResult ? (
                          <span
                            className={`gh-result ${ghPushResult.success ? "gh-ok" : "gh-err"}`}
                          >
                            {ghPushResult.success ? (
                              <a
                                href={ghPushResult.repo_url ?? "#"}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                {ghPushResult.repo_url}
                              </a>
                            ) : (
                              ghPushResult.error
                            )}
                          </span>
                        ) : null}
                      </div>

                      {/* File listing */}
                      <div className="file-browser-header">
                        <span className="file-browser-path mono">
                          /{deliverablePath || selectedProject.name}
                        </span>
                        {deliverablePath ? (
                          <button
                            className="btn-ghost"
                            onClick={handleNavigateUp}
                            type="button"
                          >
                            &larr; Up
                          </button>
                        ) : null}
                      </div>
                      {deliverableFiles.length === 0 ? (
                        <div className="empty-panel">
                          No deliverable files found. The worker may not
                          have produced output yet.
                        </div>
                      ) : (
                        <div className="file-list">
                          {deliverableFiles.map((entry) => (
                            <button
                              key={entry.path}
                              className="file-entry"
                              type="button"
                              onClick={() =>
                                entry.is_dir
                                  ? handleNavigateDir(entry.path)
                                  : void handleOpenFile(entry.path)
                              }
                            >
                              <span className="file-icon">
                                {entry.is_dir ? "\u{1F4C1}" : "\u{1F4C4}"}
                              </span>
                              <span className="file-name">
                                {entry.name}
                              </span>
                              {entry.size !== null ? (
                                <span className="file-size mono">
                                  {entry.size < 1024
                                    ? `${entry.size} B`
                                    : `${(entry.size / 1024).toFixed(1)} KB`}
                                </span>
                              ) : null}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : null}

              {/* === Activity === */}
              {mainView === "activity" ? (
                <div>
                  <div className="activity-header">
                    <span className="activity-title">
                      {selectedProject
                        ? `${selectedProject.name} — ${events.length} events`
                        : "Select a project"}
                    </span>
                  </div>
                  <div className="activity-list">
                    {events.map((event) => (
                      <div className="evt-row" key={event.id}>
                        <span
                          className="evt-dot"
                          data-tone={statusToneFor(event.level)}
                        />
                        <div className="evt-content">
                          <div className="evt-head">
                            <span className="evt-title">
                              {eventTitle(event)}
                            </span>
                            <span className="evt-time">
                              {friendlyTime(event.occurred_at)}
                            </span>
                          </div>
                          {eventDetail(event) ? (
                            <span className="evt-detail">
                              {eventDetail(event)}
                            </span>
                          ) : null}
                          <div className="evt-meta">
                            <span className="evt-tag">
                              {event.event_type}
                            </span>
                            {event.session_id ? (
                              <span className="evt-tag">
                                session {shortId(event.session_id)}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    ))}
                    {events.length === 0 ? (
                      <div className="empty-panel">
                        Select a project to stream its activity.
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
          </main>
        </div>
      )}
    </div>
  );
}
