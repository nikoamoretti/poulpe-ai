import type { HealthResponse } from "@/lib/types";

const apiBaseUrl =
  process.env.INTERNAL_API_BASE_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://localhost:8000";

export async function getApiHealth(): Promise<HealthResponse> {
  try {
    const response = await fetch(`${apiBaseUrl}/api/v1/health`, {
      cache: "no-store",
    });

    if (!response.ok) {
      return {
        status: "unavailable",
        service: "Orchestrator API",
        version: "unknown",
      };
    }

    return (await response.json()) as HealthResponse;
  } catch {
    return {
      status: "unreachable",
      service: "Orchestrator API",
      version: "unknown",
    };
  }
}
