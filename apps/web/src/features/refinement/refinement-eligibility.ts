// Pure eligibility rule for showing the refinement panel on a result page
// (spec §26): version 1, no version 2 yet, no refinement currently running,
// and refinement generation must be available. Kept separate from
// RefinementPanel.tsx (the form itself) so the calling page can decide
// whether to mount the panel at all without duplicating this logic.

import { isInProgressStatus } from "@/features/generation/generation-status";
import type { DesignDraft, DesignResult } from "@/lib/api";

export function isRefinementEligible(
  result: Pick<DesignResult, "lineage">,
  design: Pick<DesignDraft, "latest_job"> | null | undefined,
  generationEnabled: boolean,
): boolean {
  if (result.lineage.kind !== "initial") return false;
  if (!generationEnabled) return false;
  const job = design?.latest_job;
  if (!job || job.generation_kind !== "refinement") return true;
  if (isInProgressStatus(job.status)) return false;
  if (job.status === "succeeded") return false; // version 2 already exists
  return true; // a resolved (non-blocking) failure — retry is allowed
}

export function isRefinementRunning(design: Pick<DesignDraft, "latest_job"> | null | undefined): boolean {
  const job = design?.latest_job;
  return Boolean(job && job.generation_kind === "refinement" && isInProgressStatus(job.status));
}

export function isRefinementFailed(design: Pick<DesignDraft, "latest_job"> | null | undefined): boolean {
  const job = design?.latest_job;
  return Boolean(job && job.generation_kind === "refinement" && job.status === "failed");
}

// Phase 17: the result page shows a locked state where the form would be, so
// "one refinement, already used" is said rather than left to be inferred from
// a missing panel. This is the same condition `isRefinementEligible` rejects
// on — named separately so the page can tell that reason apart from
// "generation is not currently available", which needs different words.
export function isRefinementUsed(design: Pick<DesignDraft, "latest_job"> | null | undefined): boolean {
  const job = design?.latest_job;
  return Boolean(job && job.generation_kind === "refinement" && job.status === "succeeded");
}

// The version a completed refinement produced, when the server confirmed one.
// Used only to offer a link to the user's own refined concept; ownership is
// still enforced by the result route, never by this value's presence.
export function refinedVersionId(
  design: Pick<DesignDraft, "latest_job"> | null | undefined,
): string | null {
  const job = design?.latest_job;
  if (!job || job.generation_kind !== "refinement" || job.status !== "succeeded") return null;
  return job.design_version_id ?? null;
}
