"use client";

// The refinement offer, in whichever of its states applies: the form, or a
// locked panel that says which reason it is withheld for.
//
// Extracted from DesignResult when the budget went from one round to three.
// Before that, a refined version could never be refined again, so the offer
// only ever had to appear on the initial concept's page — DesignResult owned
// it, and a refined version was routed straight to VersionComparison with no
// refinement UI at all. Now round two starts from round one's output, so the
// same offer has to appear in both places, saying the same things.

import { RefinementPanel } from "./RefinementPanel";
import {
  isRefinementBudgetSpent,
  isRefinementEligible,
  isSupersededVersion,
  refinedVersionId,
} from "./refinement-eligibility";
import { friendlyGenerationError } from "@/features/generation/generation-errors";
import type { DesignDraft, DesignResult } from "@/lib/api";

type Props = {
  designId: string;
  result: Pick<DesignResult, "design_version_id" | "refinements_remaining" | "is_demo">;
  design: Pick<DesignDraft, "latest_job"> | null | undefined;
  /** Both the design and the config query have answered. */
  answered: boolean;
  generationOffered: boolean;
  running: boolean;
  failed: boolean;
  onRequiresRecheck?: () => void;
};

export function RefinementSection({
  designId,
  result,
  design,
  answered,
  generationOffered,
  running,
  failed,
  onRequiresRecheck,
}: Props) {
  const eligible = answered && isRefinementEligible(result, design, generationOffered);
  // Locked, not merely absent: once everything is known and the form is not on
  // offer, the page says which reason applies.
  const locked = answered && !eligible && !running && !failed;
  const job = design?.latest_job;
  const spent = isRefinementBudgetSpent(result);
  const superseded = isSupersededVersion(result, design);
  const latestId = refinedVersionId(design);

  // The notices STACK with the form rather than replacing it. A resolved
  // failure is eligible — the whole point of treating it as resolved is that
  // the customer may try again — so returning early on `failed` would show her
  // what went wrong and take away the control for doing something about it.
  return (
    <>
      {running && job && (
        <div role="status" aria-live="polite" className="refinement-running-notice">
          <p>
            A refinement is currently running.{" "}
            <a href={`/design/${designId}/generation/${job.id}`}>View refinement progress</a>
          </p>
        </div>
      )}

      {failed && job && (
        <div role="alert" className="refinement-failed-notice">
          <h2>{friendlyGenerationError(job.error_code).heading}</h2>
          <p>{friendlyGenerationError(job.error_code).message}</p>
        </div>
      )}

      {eligible && (
        <RefinementPanel
          designId={designId}
          sourceVersionId={result.design_version_id}
          isDemo={result.is_demo}
          refinementsRemaining={result.refinements_remaining}
          onRequiresRecheck={onRequiresRecheck}
        />
      )}

      {locked && (
        <section
          className="refinement-panel refinement-locked"
          aria-labelledby="refinement-locked-heading"
        >
          <h2 id="refinement-locked-heading">Refinement</h2>
          {spent ? (
            <p>
              You have used every refinement for this design. Sitara allows a limited number of
              constrained changes, so there is nothing further to request here.
            </p>
          ) : superseded ? (
            // Checked after `spent`, because a design with no budget left has
            // nothing to offer on its newest version either, and "carry on
            // from the latest one" would be an invitation to a dead end.
            <p>
              This is an earlier version of your design. Refinements continue from your most
              recent concept, so there is nothing to change here.
            </p>
          ) : (
            <p>
              Concept generation is not currently available, so this concept cannot be refined.
            </p>
          )}
          {superseded && latestId && (
            <p>
              <a href={`/design/${designId}/result/${latestId}`}>View your most recent concept</a>
            </p>
          )}
          <p>To take the design somewhere else, edit your answers and start a new concept.</p>
          <div className="refinement-actions">
            <a className="btn btn-secondary" href={`/design/${designId}`}>
              Edit answers
            </a>
          </div>
        </section>
      )}
    </>
  );
}
