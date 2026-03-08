import { DashboardShell } from "@/components/dashboard-shell";
import { getApiHealth } from "@/lib/api";
import type { CapabilityCard } from "@/lib/types";

export const dynamic = "force-dynamic";

const capabilityCards: CapabilityCard[] = [
  {
    title: "Control Plane",
    status: "scaffolded",
    description:
      "FastAPI stubs define the project, task, session, review, and event boundaries.",
    items: ["REST routes wired", "WebSocket endpoints stubbed", "Typed service layer in place"],
  },
  {
    title: "Repo Isolation",
    status: "stubbed",
    description:
      "Worker sessions will get isolated git branches and worktrees managed by dedicated adapters.",
    items: ["Branch naming strategy defined", "Worktree path strategy defined", "Git operations deferred"],
  },
  {
    title: "Event Stream",
    status: "scaffolded",
    description:
      "A versioned envelope and session-output block format are defined for live state tracking.",
    items: ["Structured event schema", "Parser adapter placeholder", "Redis fanout deferred"],
  },
  {
    title: "Review Gate",
    status: "stubbed",
    description:
      "Diff, lint, test, reviewer, and explicit human approval are modeled but not automated yet.",
    items: ["Review state model", "Human approval requirement captured", "Merge-ready policy documented"],
  },
];

const apiCards: CapabilityCard[] = [
  {
    title: "Projects API",
    status: "scaffolded",
    description: "Project routes exist for listing, creation, and lookup.",
    items: ["GET /api/v1/projects", "POST /api/v1/projects", "GET /api/v1/projects/{id}"],
  },
  {
    title: "Sessions API",
    status: "scaffolded",
    description: "Session lifecycle routes exist with worker/manager/reviewer roles.",
    items: ["GET /api/v1/sessions", "POST /api/v1/sessions", "POST /api/v1/sessions/{id}/stop"],
  },
  {
    title: "Live Streams",
    status: "stubbed",
    description: "WebSocket routes are present but currently return one-shot stub messages.",
    items: ["WS /ws/projects/{project_id}/events", "WS /ws/sessions/{session_id}/output"],
  },
];

export default async function HomePage() {
  const health = await getApiHealth();

  return (
    <DashboardShell
      health={health}
      capabilityCards={capabilityCards}
      apiCards={apiCards}
    />
  );
}

