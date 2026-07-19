// Pure, deterministic polling-backoff helpers for the generation progress
// flow. No fetching here so this stays trivially unit-testable. Lifecycle
// navigation (resolveDesignLifecycleTarget) lives in lib/design-lifecycle.ts
// since both features/questionnaire and features/generation depend on it.

import type { GenerationJob } from "@/lib/api";

// The statuses that count as "generation still in progress", mirroring the
// backend's GenerationAttempt.IN_PROGRESS_STATUSES.
const IN_PROGRESS_STATUSES: ReadonlySet<GenerationJob["status"]> = new Set([
  "queued",
  "running_text",
  "running_image",
]);

export function isInProgressStatus(status: GenerationJob["status"]): boolean {
  return IN_PROGRESS_STATUSES.has(status);
}

export function isTerminalStatus(status: GenerationJob["status"]): boolean {
  return status === "succeeded" || status === "failed";
}

// Polling schedule (spec §14): <10s since job creation -> 1s; 10-30s -> 2s;
// after 30s -> 5s; terminal states stop polling entirely (null).
export function pollingIntervalMs(
  status: GenerationJob["status"],
  createdAt: string,
  now: number,
): number | false {
  if (isTerminalStatus(status)) return false;
  const created = Date.parse(createdAt);
  if (Number.isNaN(created)) return 5000; // malformed timestamp: fall back to the coarsest band
  const elapsedMs = now - created;
  if (elapsedMs < 10_000) return 1000;
  if (elapsedMs < 30_000) return 2000;
  return 5000;
}
