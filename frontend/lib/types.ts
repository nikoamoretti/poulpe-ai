export type HealthResponse = {
  status: string;
  service: string;
  version: string;
};

export type CapabilityCard = {
  title: string;
  status: "ready" | "scaffolded" | "stubbed";
  description: string;
  items: string[];
};

