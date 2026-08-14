// Pure eligibility rule for showing the refinement panel on a result page
// (spec §26): the design has refinements left, THIS version is the latest one,
// no refinement is currently running, and refinement generation must be
// available. Kept separate from RefinementPanel.tsx (the form itself) so the
// calling page can decide whether to mount the panel at all without
// duplicating this logic.
//
// Both halves used to be inferred. "Has it been refined?" came from
// `lineage.kind !== "initial"`, and "is the budget spent?" from a succeeded
// refinement job — inferences that were only ever right because the answer was
// one. With three, the budget is read from the server's own
// `refinements_remaining` count, and "is this the latest version?" is decided
// by comparing the succeeded job's produced version against this one.

import { isInProgressStatus } from "@/features/generation/generation-status";
import type { DesignDraft, DesignResult } from "@/lib/api";

type ResultFacts = Pick<DesignResult, "design_version_id" | "refinements_remaining">;
type DesignFacts = Pick<DesignDraft, "latest_job"> | null | undefined;

// A newer version exists, so this one is somewhere back up the chain. Refining
// it would give one parent two children, which the server refuses and the
// version numbering has no way to describe. Read from the latest job rather
// than from a version list the result payload does not carry: a SUCCEEDED
// refinement names the version it produced, and if that is not this one then
// this one has been superseded.
export function isSupersededVersion(result: ResultFacts, design: DesignFacts): boolean {
  const job = design?.latest_job;
  if (!job || job.generation_kind !== "refinement" || job.status !== "succeeded") return false;
  if (!job.design_version_id) return false;
  return job.design_version_id !== result.design_version_id;
}

export function isRefinementEligible(
  result: ResultFacts,
  design: DesignFacts,
  generationEnabled: boolean,
): boolean {
  if (!generationEnabled) return false;
  if (result.refinements_remaining <= 0) return false;
  if (isSupersededVersion(result, design)) return false;
  const job = design?.latest_job;
  if (!job || job.generation_kind !== "refinement") return true;
  if (isInProgressStatus(job.status)) return false;
  return true; // succeeded on THIS version, or a resolved failure — both allow another round
}

export function isRefinementRunning(design: DesignFacts): boolean {
  const job = design?.latest_job;
  return Boolean(job && job.generation_kind === "refinement" && isInProgressStatus(job.status));
}

export function isRefinementFailed(design: DesignFacts): boolean {
  const job = design?.latest_job;
  return Boolean(job && job.generation_kind === "refinement" && job.status === "failed");
}

// Phase 17: the result page shows a locked state where the form would be, so
// "no refinements left" is said rather than left to be inferred from a missing
// panel. This is one of the conditions `isRefinementEligible` rejects on —
// named separately so the page can tell it apart from "generation is not
// currently available" and from "a newer version exists", which need
// different words.
export function isRefinementBudgetSpent(result: ResultFacts): boolean {
  return result.refinements_remaining <= 0;
}

// The version a completed refinement produced, when the server confirmed one.
// Used only to offer a link to the user's own refined concept; ownership is
// still enforced by the result route, never by this value's presence.
export function refinedVersionId(design: DesignFacts): string | null {
  const job = design?.latest_job;
  if (!job || job.generation_kind !== "refinement" || job.status !== "succeeded") return null;
  return job.design_version_id ?? null;
}
