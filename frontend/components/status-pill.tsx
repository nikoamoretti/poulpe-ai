import type { ReactNode } from "react";

type StatusPillProps = {
  tone: "ready" | "scaffolded" | "stubbed";
  children: ReactNode;
};

export function StatusPill({ tone, children }: StatusPillProps) {
  return (
    <span className="pill" data-tone={tone}>
      <span className="pill-dot" />
      {children}
    </span>
  );
}
