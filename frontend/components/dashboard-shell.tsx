"use client";

import { type FormEvent, useEffect, useState, useTransition } from "react";

import { StatusPill, statusToneFor } from "@/components/status-pill";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  createPortfolio,
  createProject,
  getApiHealth,
  getSession,
  listPortfolioInbox,
  listPortfolios,
  listProjectEvents,
  listProjects,
  listSessions,
  respondToPortfolioCheckpoint,
  sendProjectManagerInstruction,
  startPortfolioManager,
  startProjectExecution,
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
type MainView = "workspace" | "inbox" | "activity";

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
    "project.checkpoint_opened": "Manager checkpoint opened",
    "project.checkpoint_resolved": "Manager checkpoint resolved",
    "session.start": "Session booted",
    "session.started": "Session started",
    "session.progress": "Progress update",
    "session.question": "Question raised",
    "session.blocked": "Blocked",
    "session.tests_run": "Verification ran",
    "session.complete": "Completion claimed",
    "session.completed": "Session completed",
    "session.error": "Session error",
    "session.failed": "Session failed",
    "worktree.ready": "Workspace ready",
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
    "No additional detail."
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
  const [, startTransition] = useTransition();

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

  const workflowSteps = [
    {
      title: "Select a portfolio",
      detail: "Everything lives inside one portfolio.",
      done: selectedPortfolio !== null,
    },
    {
      title: "Add a project",
      detail: "Each project gets its own worker session.",
      done: portfolioProjectCount > 0,
    },
    {
      title: "Start the manager",
      detail: "The manager handles questions and reviews completion claims.",
      done: managerSession !== null,
    },
    {
      title: "Start a project session",
      detail: "Launch the selected project when you are ready to work.",
      done: activeWorkerCount > 0,
    },
  ];

  let nextActionTitle = "Create a portfolio";
  let nextActionDetail = "Start by creating or selecting a portfolio in the left rail.";
  if (selectedPortfolio) {
    nextActionTitle = "Add your first project";
    nextActionDetail = "Create a project from a name, then Poulpe will create its local repo for you.";
  }
  if (selectedPortfolio && portfolioProjectCount > 0) {
    nextActionTitle = "Start the portfolio manager";
    nextActionDetail = "The manager session supervises workers, answers questions, and reviews completions.";
  }
  if (selectedPortfolio && portfolioProjectCount > 0 && managerSession) {
    nextActionTitle = "Start the selected project";
    nextActionDetail = selectedProject
      ? `Launch ${selectedProject.name} when you want its worker session to start operating.`
      : "Pick a project card, then start its worker session.";
  }
  if (selectedPortfolio && selectedWorker && selectedWorker.status !== "pending") {
    nextActionTitle = openCheckpointCount > 0 ? "Review the manager inbox" : "Guide the selected project";
    nextActionDetail =
      openCheckpointCount > 0
        ? "Open the inbox tab to answer worker questions or approve completion claims."
        : "Use the selected-project panel to send corrections or new instructions.";
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

    await runAction("Instruction sent to project", async () => {
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

  return (
    <div className="shell portfolio-shell">
      <header className="topbar">
        <div className="topbar-left">
          <span className="topbar-dot" data-status={connectionStatus} />
          <span className="topbar-brand">Poulpe Portfolio Console</span>
          <span className="topbar-tag">
            {selectedPortfolio ? selectedPortfolio.slug : "no portfolio"}
          </span>
          {refreshing ? <span className="board-live">syncing</span> : null}
        </div>
        <div className="topbar-right">
          <ThemeToggle />
        </div>
      </header>

      <main className="portfolio-layout">
        <aside className="portfolio-sidebar">
          <section className="sidebar-card sidebar-guide">
            <div className="section-head">
              <div>
                <p className="section-kicker">How to use</p>
                <h2 className="section-title">4-step flow</h2>
              </div>
              <StatusPill tone={statusToneFor(health.status)}>{health.status}</StatusPill>
            </div>
            <p className="section-copy">
              One manager session supervises one independent worker session per project.
            </p>
            <div className="guide-list">
              {workflowSteps.map((step, index) => (
                <div
                  className={`guide-step ${step.done ? "is-done" : ""}`}
                  key={step.title}
                >
                  <span className="guide-step-index">{index + 1}</span>
                  <div className="guide-step-body">
                    <strong>{step.title}</strong>
                    <span>{step.detail}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="sidebar-card">
            <div className="section-head">
              <div>
                <p className="section-kicker">Portfolios</p>
                <h2 className="section-title">{portfolios.length}</h2>
              </div>
            </div>
            <div className="portfolio-list">
              {portfolios.map((portfolio) => {
                const selected = portfolio.id === selectedPortfolioId;
                return (
                  <button
                    key={portfolio.id}
                    className={`portfolio-item ${selected ? "is-selected" : ""}`}
                    type="button"
                    onClick={() => setSelectedPortfolioId(portfolio.id)}
                  >
                    <div className="portfolio-item-head">
                      <span>{portfolio.name}</span>
                      <StatusPill tone={statusToneFor(portfolio.status)} compact>
                        {portfolio.status}
                      </StatusPill>
                    </div>
                    <p>{portfolio.goal || "No portfolio goal recorded yet."}</p>
                  </button>
                );
              })}
              {portfolios.length === 0 && !loading ? (
                <div className="empty-block">
                  Create the first portfolio to start the manager/project model.
                </div>
              ) : null}
            </div>
          </section>

          <section className="sidebar-card">
            <div className="section-head">
              <div>
                <p className="section-kicker">Create</p>
                <h2 className="section-title">New portfolio</h2>
              </div>
            </div>
            <form className="stack-form" onSubmit={handleCreatePortfolio}>
              <label className="field">
                <span>Name</span>
                <input
                  className="field-input"
                  value={portfolioForm.name}
                  onChange={(event) =>
                    setPortfolioForm((current) => ({
                      ...current,
                      name: event.target.value,
                    }))
                  }
                  placeholder="Platform modernization"
                />
              </label>
              <label className="field">
                <span>Goal</span>
                <textarea
                  className="field-textarea"
                  value={portfolioForm.goal}
                  onChange={(event) =>
                    setPortfolioForm((current) => ({
                      ...current,
                      goal: event.target.value,
                    }))
                  }
                  rows={4}
                  placeholder="Coordinate the active project sessions and bring them to completion."
                />
              </label>
              <button className="button-primary" disabled={pendingAction !== null} type="submit">
                Create portfolio
              </button>
            </form>
          </section>
        </aside>

        <section className="portfolio-main">
          {flash ? (
            <div className={`flash-banner is-${flash.tone}`}>{flash.message}</div>
          ) : null}

          {selectedPortfolio ? (
            <>
              <section className="hero-card">
                <div className="hero-copy">
                  <p className="section-kicker">Selected portfolio</p>
                  <h1>{selectedPortfolio.name}</h1>
                  <p>{selectedPortfolio.goal || "No portfolio goal recorded yet."}</p>
                </div>
                <div className="hero-stats">
                  <div className="hero-stat">
                    <span>Projects</span>
                    <strong>{portfolioProjectCount}</strong>
                  </div>
                  <div className="hero-stat">
                    <span>Active workers</span>
                    <strong>{activeWorkerCount}</strong>
                  </div>
                  <div className="hero-stat">
                    <span>Open inbox</span>
                    <strong>{openCheckpointCount}</strong>
                  </div>
                </div>
                <div className="hero-actions">
                  <div className="hero-next-step">
                    <p className="section-kicker">Next recommended action</p>
                    <h2 className="hero-action-title">{nextActionTitle}</h2>
                    <p>{nextActionDetail}</p>
                  </div>
                  <div className="hero-action-row">
                    {managerSession ? (
                      <>
                        <div className="session-chip">
                          <span>Manager</span>
                          <strong>{shortId(managerSession.id)}</strong>
                          <span>{managerSession.status}</span>
                        </div>
                        <button
                          className="button-ghost"
                          disabled={pendingAction !== null}
                          onClick={() => void handleStartManager()}
                          type="button"
                        >
                          Restart manager
                        </button>
                      </>
                    ) : (
                      <button
                        className="button-primary"
                        disabled={pendingAction !== null}
                        onClick={() => void handleStartManager()}
                        type="button"
                      >
                        Start manager
                      </button>
                    )}
                    {selectedProject && !selectedWorker ? (
                      <button
                        className="button-secondary"
                        disabled={pendingAction !== null}
                        onClick={() => void handleStartProject(selectedProject)}
                        type="button"
                      >
                        Start selected project
                      </button>
                    ) : null}
                    {openCheckpointCount > 0 ? (
                      <button
                        className="button-secondary"
                        onClick={() => setMainView("inbox")}
                        type="button"
                      >
                        Review inbox
                      </button>
                    ) : null}
                    {selectedProject ? (
                      <button
                        className="button-ghost"
                        onClick={() => setMainView("activity")}
                        type="button"
                      >
                        View activity
                      </button>
                    ) : null}
                  </div>
                </div>
              </section>

              <div className="view-switcher">
                <button
                  className={`view-tab ${mainView === "workspace" ? "is-active" : ""}`}
                  onClick={() => setMainView("workspace")}
                  type="button"
                >
                  Workspace
                </button>
                <button
                  className={`view-tab ${mainView === "inbox" ? "is-active" : ""}`}
                  onClick={() => setMainView("inbox")}
                  type="button"
                >
                  Inbox
                  <span className="view-tab-count">{openCheckpointCount}</span>
                </button>
                <button
                  className={`view-tab ${mainView === "activity" ? "is-active" : ""}`}
                  onClick={() => setMainView("activity")}
                  type="button"
                >
                  Activity
                  <span className="view-tab-count">{selectedProject ? events.length : 0}</span>
                </button>
              </div>

              {mainView === "workspace" ? (
                <div className="board-grid board-grid-workspace">
                  <section className="panel">
                    <div className="section-head">
                      <div>
                        <p className="section-kicker">Projects</p>
                        <h2 className="section-title">Independent execution lanes</h2>
                      </div>
                    </div>
                    <p className="section-copy panel-copy">
                      Select one project, then start its worker session when you are ready.
                    </p>
                    <div className="project-grid">
                      {projects.map((project) => {
                        const worker = primaryWorkerSession(
                          project,
                          sessionsByProject[project.id] ?? [],
                        );
                        const openForProject = inbox.filter(
                          (checkpoint) => checkpoint.project_id === project.id,
                        ).length;
                        const selected = project.id === selectedProjectId;

                        return (
                          <article
                            key={project.id}
                            className={`project-card ${selected ? "is-selected" : ""}`}
                          >
                            <button
                              className="project-card-select"
                              type="button"
                              onClick={() => setSelectedProjectId(project.id)}
                            >
                              <div className="project-card-head">
                                <div>
                                  <h3>{project.name}</h3>
                                  <p>{project.objective}</p>
                                </div>
                                <StatusPill tone={statusToneFor(worker?.status ?? project.status)}>
                                  {worker?.status ?? "idle"}
                                </StatusPill>
                              </div>
                              <div className="project-meta">
                                <span>repo {compactPath(project.repo_path)}</span>
                                <span>branch {project.default_branch}</span>
                                <span>{openForProject} open checkpoint(s)</span>
                              </div>
                              <div className="project-meta">
                                <span>{worker ? `worker ${shortId(worker.id)}` : "worker not started"}</span>
                                <span>{worker?.runtime.resolved_provider ?? "runtime not selected"}</span>
                                <span>{worker?.branch_name ? `workspace ${worker.branch_name}` : "no branch yet"}</span>
                              </div>
                              {project.completion_summary ? (
                                <div className="completion-note">
                                  {project.completion_summary}
                                </div>
                              ) : null}
                            </button>
                            <div className="project-actions">
                              <button
                                className="button-secondary"
                                disabled={pendingAction !== null}
                                onClick={() => void handleStartProject(project)}
                                type="button"
                              >
                                {worker ? "Restart project" : "Start project"}
                              </button>
                            </div>
                          </article>
                        );
                      })}

                      {projects.length === 0 ? (
                        <div className="empty-block">
                          Add the first project in this portfolio to create its dedicated repo and worker lane.
                        </div>
                      ) : null}
                    </div>
                  </section>

                  <div className="workspace-stack">
                    <section className="panel">
                      <div className="section-head">
                        <div>
                          <p className="section-kicker">Selected project</p>
                          <h2 className="section-title">
                            {selectedProject ? selectedProject.name : "Pick a project"}
                          </h2>
                        </div>
                        {selectedWorker ? (
                          <StatusPill tone={statusToneFor(selectedWorker.status)}>
                            {selectedWorker.status}
                          </StatusPill>
                        ) : null}
                      </div>

                      {selectedProject ? (
                        <div className="project-focus">
                          <div className="focus-grid">
                            <div className="focus-card">
                              <p className="focus-label">Objective</p>
                              <p>{selectedProject.objective}</p>
                            </div>
                            <div className="focus-card">
                              <p className="focus-label">Open checkpoints</p>
                              <p>{selectedProjectCheckpointCount || "No open manager inbox items"}</p>
                            </div>
                            <div className="focus-card">
                              <p className="focus-label">Worker</p>
                              <p>
                                {selectedWorker
                                  ? `${shortId(selectedWorker.id)} · ${selectedWorker.runtime.resolved_provider}`
                                  : "No worker session started"}
                              </p>
                            </div>
                            <div className="focus-card">
                              <p className="focus-label">Repo</p>
                              <p>{selectedProject.repo_path}</p>
                            </div>
                          </div>

                          <label className="field">
                            <span>Manager instruction</span>
                            <textarea
                              className="field-textarea"
                              rows={4}
                              value={instructionDraft}
                              onChange={(event) => setInstructionDraft(event.target.value)}
                              placeholder="Tell the selected worker what to do next or how to correct course."
                            />
                          </label>
                          <div className="action-row">
                            <button
                              className="button-primary"
                              disabled={pendingAction !== null}
                              onClick={() => void handleSendInstruction()}
                              type="button"
                            >
                              Send instruction
                            </button>
                            <button
                              className="button-secondary"
                              disabled={pendingAction !== null}
                              onClick={() => void handleStartProject(selectedProject)}
                              type="button"
                            >
                              {selectedWorker ? "Restart project session" : "Start project session"}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="empty-block">
                          Pick a project card on the left to see its details and send instructions.
                        </div>
                      )}
                    </section>

                    <section className="panel">
                      <div className="section-head">
                        <div>
                          <p className="section-kicker">Create</p>
                          <h2 className="section-title">Add project</h2>
                        </div>
                      </div>
                      <p className="section-copy panel-copy">
                        Use a name only for a new repo, or turn auto-create off to point at an existing repo.
                      </p>
                      <form className="stack-form" onSubmit={handleCreateProject}>
                        <label className="field">
                          <span>Name</span>
                          <input
                            className="field-input"
                            value={projectForm.name}
                            onChange={(event) =>
                              setProjectForm((current) => ({
                                ...current,
                                name: event.target.value,
                              }))
                            }
                            placeholder="Auth API hardening"
                          />
                        </label>
                        <div className="field">
                          <span>Repository</span>
                          <label className="field-inline">
                            <input
                              checked={projectForm.createRepo}
                              onChange={(event) =>
                                setProjectForm((current) => ({
                                  ...current,
                                  createRepo: event.target.checked,
                                }))
                              }
                              type="checkbox"
                            />
                            <span>Create a new local repo automatically</span>
                          </label>
                        </div>
                        <label className="field">
                          <span>Repo path</span>
                          <input
                            className="field-input"
                            disabled={projectForm.createRepo}
                            value={projectForm.repoPath}
                            onChange={(event) =>
                              setProjectForm((current) => ({
                                ...current,
                                repoPath: event.target.value,
                              }))
                            }
                            placeholder={
                              projectForm.createRepo
                                ? "Created automatically from the project name"
                                : "/Users/nico-yardlogix/some-repo"
                            }
                          />
                        </label>
                        <label className="field-inline">
                          <span>Default branch</span>
                          <input
                            className="field-input"
                            value={projectForm.defaultBranch}
                            onChange={(event) =>
                              setProjectForm((current) => ({
                                ...current,
                                defaultBranch: event.target.value,
                              }))
                            }
                            placeholder="main"
                          />
                        </label>
                        <label className="field">
                          <span>Objective (optional)</span>
                          <textarea
                            className="field-textarea"
                            value={projectForm.objective}
                            onChange={(event) =>
                              setProjectForm((current) => ({
                                ...current,
                                objective: event.target.value,
                              }))
                            }
                            rows={4}
                            placeholder="Leave blank to use the default project brief."
                          />
                        </label>
                        <button
                          className="button-primary"
                          disabled={pendingAction !== null}
                          type="submit"
                        >
                          Add project
                        </button>
                      </form>
                    </section>
                  </div>
                </div>
              ) : null}

              {mainView === "inbox" ? (
                <section className="panel panel-wide">
                  <div className="section-head">
                    <div>
                      <p className="section-kicker">Inbox</p>
                      <h2 className="section-title">Manager inbox</h2>
                    </div>
                    <StatusPill tone={statusToneFor(openCheckpointCount > 0 ? "warning" : "success")}>
                      {openCheckpointCount} open
                    </StatusPill>
                  </div>
                  <p className="section-copy panel-copy">
                    Open items appear here whenever a worker asks for help, gets blocked, or claims completion.
                  </p>
                  <div className="checkpoint-list">
                    {inbox.map((checkpoint) => {
                      const details = asRecord(checkpoint.details);
                      const reviewContext = asRecord(details.review_context);
                      const diffDetails = asRecord(reviewContext.diff);
                      const checks = asArray(reviewContext.checks);
                      const draft = checkpointDrafts[checkpoint.id] ?? "";

                      return (
                        <article className="checkpoint-card" key={checkpoint.id}>
                          <div className="checkpoint-head">
                            <div>
                              <p className="checkpoint-project">{checkpoint.project_name}</p>
                              <h3>{checkpoint.summary}</h3>
                            </div>
                            <div className="checkpoint-meta">
                              <StatusPill tone={statusToneFor(checkpoint.kind)}>
                                {checkpoint.kind}
                              </StatusPill>
                              <span>{friendlyTime(checkpoint.source_occurred_at)}</span>
                            </div>
                          </div>

                          {asString(reviewContext.error) ? (
                            <p className="checkpoint-warning">{asString(reviewContext.error)}</p>
                          ) : null}

                          {checkpoint.kind === "completion" ? (
                            <div className="review-context">
                              <div className="review-stats">
                                <span>{asString(diffDetails.summary) ?? "No diff summary"}</span>
                                <span>
                                  {asArray(diffDetails.changed_files).length} changed file(s)
                                </span>
                                <span>{checks.length} verification artifact(s)</span>
                              </div>
                              {asArray(diffDetails.changed_files).length > 0 ? (
                                <div className="tag-list">
                                  {asArray(diffDetails.changed_files).map((item) => (
                                    <span className="file-tag" key={String(item)}>
                                      {String(item)}
                                    </span>
                                  ))}
                                </div>
                              ) : null}
                              {asString(diffDetails.diff_preview) ? (
                                <pre className="diff-preview">
                                  {String(diffDetails.diff_preview)}
                                </pre>
                              ) : null}
                              {checks.length > 0 ? (
                                <div className="check-list">
                                  {checks.map((check, index) => {
                                    const checkRecord = asRecord(check);
                                    return (
                                      <div className="check-row" key={`${checkpoint.id}-${index}`}>
                                        <StatusPill tone={statusToneFor(asString(checkRecord.status) ?? "info")} compact>
                                          {asString(checkRecord.kind) ?? "check"}
                                        </StatusPill>
                                        <span>{asString(checkRecord.command) ?? "unknown command"}</span>
                                        <span>{asString(checkRecord.status) ?? "unknown"}</span>
                                      </div>
                                    );
                                  })}
                                </div>
                              ) : null}
                            </div>
                          ) : null}

                          <label className="field">
                            <span>Manager response</span>
                            <textarea
                              className="field-textarea"
                              rows={3}
                              value={draft}
                              onChange={(event) =>
                                setCheckpointDrafts((current) => ({
                                  ...current,
                                  [checkpoint.id]: event.target.value,
                                }))
                              }
                              placeholder={
                                checkpoint.kind === "completion"
                                  ? "Request changes or leave blank to approve."
                                  : "Answer the project session so it can continue."
                              }
                            />
                          </label>

                          <div className="checkpoint-actions">
                            {checkpoint.kind === "completion" ? (
                              <>
                                <button
                                  className="button-primary"
                                  disabled={pendingAction !== null}
                                  onClick={() =>
                                    void handleCheckpointAction(checkpoint, "approve")
                                  }
                                  type="button"
                                >
                                  Approve
                                </button>
                                <button
                                  className="button-secondary"
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
                                className="button-primary"
                                disabled={pendingAction !== null}
                                onClick={() =>
                                  void handleCheckpointAction(checkpoint, "answer")
                                }
                                type="button"
                              >
                                Answer
                              </button>
                            )}
                            <button
                              className="button-ghost"
                              disabled={pendingAction !== null}
                              onClick={() =>
                                void handleCheckpointAction(checkpoint, "dismiss")
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
                      <div className="empty-block">
                        No open manager inbox items. Start or continue projects and new
                        questions, blockers, or completion claims will appear here.
                      </div>
                    ) : null}
                  </div>
                </section>
              ) : null}

              {mainView === "activity" ? (
                <section className="panel panel-wide">
                  <div className="section-head">
                    <div>
                      <p className="section-kicker">Activity</p>
                      <h2 className="section-title">
                        {selectedProject ? `${selectedProject.name} event feed` : "Project feed"}
                      </h2>
                    </div>
                  </div>
                  <p className="section-copy panel-copy">
                    This is the latest event stream for the currently selected project.
                  </p>
                  <div className="activity-list">
                    {events.map((event) => (
                      <div className="activity-row" key={event.id}>
                        <div className="activity-marker" data-tone={statusToneFor(event.level)} />
                        <div className="activity-copy">
                          <div className="activity-head">
                            <strong>{eventTitle(event)}</strong>
                            <span>{friendlyTime(event.occurred_at)}</span>
                          </div>
                          <p>{eventDetail(event)}</p>
                          <div className="activity-meta">
                            <span>{event.event_type}</span>
                            {event.session_id ? <span>session {shortId(event.session_id)}</span> : null}
                          </div>
                        </div>
                      </div>
                    ))}
                    {events.length === 0 ? (
                      <div className="empty-block">
                        Select a project to stream activity here.
                      </div>
                    ) : null}
                  </div>
                </section>
              ) : null}
            </>
          ) : (
            <section className="hero-card is-empty">
              <div className="hero-copy">
                <p className="section-kicker">Portfolio view</p>
                <h1>No portfolio selected</h1>
                <p>
                  Create a portfolio in the left sidebar, then add independent projects
                  and start a manager session to supervise them.
                </p>
              </div>
            </section>
          )}
        </section>
      </main>
    </div>
  );
}
