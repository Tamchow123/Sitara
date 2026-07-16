export function apiBaseUrl(): string {
  // NEXT_PUBLIC_* values are public by definition; never put secrets here.
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
}

export type ReadyChecks = {
  database: string;
  redis: string;
  storage: string;
};

export type ReadyResponse = {
  status: string;
  checks: ReadyChecks;
};

export type PublicConfig = {
  demo_mode: boolean;
  generation_enabled: boolean;
  max_inspiration_images: number;
  max_refinements: number;
};

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  // Readiness intentionally returns 503 with a JSON body when a dependency
  // is down — that is still displayable state, not a thrown error.
  return (await response.json()) as T;
}

export function fetchReadiness(): Promise<ReadyResponse> {
  return getJson<ReadyResponse>("/api/v1/health/ready");
}

export function fetchPublicConfig(): Promise<PublicConfig> {
  return getJson<PublicConfig>("/api/v1/config/public");
}
