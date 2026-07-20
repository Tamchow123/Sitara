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
