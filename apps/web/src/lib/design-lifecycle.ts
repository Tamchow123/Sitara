// Lifecycle-based navigation target resolution (Phase 12 §12). Lives in
// lib/, not inside features/questionnaire or features/generation, because
// both features depend on it — putting it inside either feature folder
// would create a two-way feature-to-feature dependency. Feature-agnostic by
// construction: it reads directly from the generated OpenAPI schema rather
// than importing a feature's own type alias.

import type { components } from "@/api/schema";

type DesignLifecycleInput = Pick<
  components["schemas"]["DesignDetailResponse"],
  "id" | "status" | "latest_job"
>;

type GenerationJobStatus = NonNullable<
  components["schemas"]["DesignDetailResponse"]["latest_job"]
>["status"];

// The statuses that count as "generation still in progress", mirroring the
// backend's GenerationAttempt.IN_PROGRESS_STATUSES.
const IN_PROGRESS_STATUSES: ReadonlySet<GenerationJobStatus> = new Set([
  "queued",
  "running_text",
  "running_image",
]);

export type LifecycleTarget =
  | { kind: "questionnaire" }
  | { kind: "progress"; href: string }
  | { kind: "result"; href: string }
  | { kind: "unavailable" };

// Resolves an owned design's current status + latest_job into exactly one
// durable navigation target. Never guesses an id: a route is only ever
// built from ids the server actually returned on this design/job pair.
export function resolveDesignLifecycleTarget(design: DesignLifecycleInput): LifecycleTarget {
  const { status, latest_job: job, id } = design;

  if (status === "draft") return { kind: "questionnaire" };

  if (status === "generation_failed") {
    // No linked DesignVersion: the draft remains editable (services.py
    // returns generation_failed designs with no version to "draft" on edit).
    if (!job || !job.design_version_id) return { kind: "questionnaire" };
    return { kind: "progress", href: `/design/${id}/generation/${job.id}` };
  }

  if (status === "generating") {
    if (job && IN_PROGRESS_STATUSES.has(job.status)) {
      return { kind: "progress", href: `/design/${id}/generation/${job.id}` };
    }
    // "generating" with no coherent in-progress job is an inconsistent
    // lifecycle state — never guess which job to show.
    return { kind: "unavailable" };
  }

  if (status === "generated") {
    if (job && job.status === "succeeded" && job.design_version_id) {
      return { kind: "result", href: `/design/${id}/result/${job.design_version_id}` };
    }
    return { kind: "unavailable" };
  }

  return { kind: "unavailable" };
}
