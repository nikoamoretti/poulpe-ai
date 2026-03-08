import { StatusPill } from "@/components/status-pill";
import type { CapabilityCard, HealthResponse } from "@/lib/types";

type DashboardShellProps = {
  health: HealthResponse;
  capabilityCards: CapabilityCard[];
  apiCards: CapabilityCard[];
};

const timeline = [
  {
    title: "Scaffolded now",
    detail: "Repo structure, schema contracts, route stubs, prompt templates, and dashboard shell.",
  },
  {
    title: "Next backend slice",
    detail: "Persist projects, tasks, sessions, and events in Postgres; wire real create/list flows.",
  },
  {
    title: "Then orchestration",
    detail: "Supervise local worker processes, create git worktrees, stream output, and trigger reviews.",
  },
];

export function DashboardShell({
  health,
  capabilityCards,
  apiCards,
}: DashboardShellProps) {
  const backendTone = health.status === "ok" ? "ready" : "stubbed";

  return (
    <main className="page-shell">
      <div className="page-grid">
        <section className="hero">
          <div className="hero-top">
            <div>
              <div className="eyebrow">Local-First Multi-Agent Orchestrator</div>
              <h1>Local Agent Orchestrator v0</h1>
            </div>
            <StatusPill tone={backendTone}>
              Backend {health.status === "ok" ? "reachable" : "not reached"}
            </StatusPill>
          </div>

          <p>
            One manager session supervises worker and reviewer sessions, each in its own
            git worktree. Persistent state lives in Postgres, live fanout rides Redis,
            and human approval stays in the merge path.
          </p>

          <div className="hero-meta">
            <StatusPill tone="scaffolded">{health.service}</StatusPill>
            <StatusPill tone="scaffolded">Version {health.version}</StatusPill>
            <StatusPill tone="stubbed">Merge approval still manual</StatusPill>
          </div>

          <div className="stats-grid">
            <div className="stat-card">
              <div className="code-label">Backend</div>
              <strong>FastAPI</strong>
              <p>API routes, typed services, models, migration SQL, and WebSocket stubs.</p>
            </div>
            <div className="stat-card">
              <div className="code-label">State</div>
              <strong>Postgres + Redis</strong>
              <p>Persistent state schema is defined; queue and pubsub are reserved for later phases.</p>
            </div>
            <div className="stat-card">
              <div className="code-label">Isolation</div>
              <strong>Git worktrees</strong>
              <p>Per-worker branch and worktree conventions are documented and service-scoped.</p>
            </div>
          </div>
        </section>

        <div className="two-up">
          <section className="panel">
            <div className="section-head">
              <h2>Capability map</h2>
              <p>The scaffold is split along service boundaries so persistence and orchestration can land incrementally.</p>
            </div>
            <div className="card-grid">
              {capabilityCards.map((card) => (
                <article className="card" key={card.title}>
                  <StatusPill tone={card.status}>{card.title}</StatusPill>
                  <p>{card.description}</p>
                  <ul>
                    {card.items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </article>
              ))}
            </div>
          </section>

          <aside className="rail">
            <div className="section-head">
              <h2>Implementation rail</h2>
              <p>What is real in the repo versus what is intentionally deferred.</p>
            </div>
            <div className="rail-list">
              {timeline.map((item) => (
                <div className="rail-item" key={item.title}>
                  <strong>{item.title}</strong>
                  <p>{item.detail}</p>
                </div>
              ))}
            </div>
          </aside>
        </div>

        <section className="panel">
          <div className="section-head">
            <h2>API surface</h2>
            <p>HTTP and WebSocket entry points are present now, with list endpoints returning empty state and mutation paths intentionally stubbed.</p>
          </div>
          <div className="card-grid">
            {apiCards.map((card) => (
              <article className="card" key={card.title}>
                <StatusPill tone={card.status}>{card.title}</StatusPill>
                <p>{card.description}</p>
                <ul>
                  {card.items.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </article>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
