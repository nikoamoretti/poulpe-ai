import type { ReactNode } from "react";

export type StatusTone =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "muted";

type StatusPillProps = {
  tone: StatusTone;
  children: ReactNode;
  compact?: boolean;
};

export function statusToneFor(value: string | null | undefined): StatusTone {
  switch (value) {
    case "ok":
    case "ready":
    case "running":
    case "completed":
    case "done":
    case "approved":
    case "active":
    case "passed":
      return "success";
    case "starting":
    case "pending":
    case "review":
    case "info":
      return "info";
    case "blocked":
    case "dirty":
    case "needs_changes":
    case "timed_out":
    case "warn":
    case "stopped":
      return "warning";
    case "failed":
    case "rejected":
    case "error":
    case "unreachable":
    case "unavailable":
      return "danger";
    case "reviewer":
    case "worker":
    case "manager":
      return "muted";
    default:
      return "neutral";
  }
}

export function StatusPill({ tone, children, compact = false }: StatusPillProps) {
  return (
    <span className="pill" data-tone={tone} data-compact={compact}>
      <span className="pill-dot" />
      {children}
    </span>
  );
}
