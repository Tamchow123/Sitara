// Friendly copy for the refinement PANEL's submit-time failures. Distinct
// from features/generation/generation-errors.ts, which covers a job's own
// terminal error_code (surfaced later, on the progress route) — these codes
// are returned synchronously by POST /designs/{id}/refine/ before any job
// exists, so they need their own small map. Never surfaces a raw backend
// exception message; falls back to the server's own safe message text for
// any code this map does not recognise.

const REFINEMENT_SUBMIT_MESSAGES: Record<string, string> = {
  refinement_invalid: "Please choose one change and check your note, then try again.",
  design_not_refinable: "This design cannot be refined right now.",
  refinement_source_unavailable:
    "The original concept is not available for refinement. Please try again shortly.",
  refinement_in_progress: "A refinement is already in progress for this design.",
  refinement_limit_reached: "This design has already been refined.",
  generation_unavailable: "Concept generation is not available right now.",
  queue_unavailable: "The generation queue is temporarily unavailable. Please try again shortly.",
};

// Codes after which the panel should stop offering itself for this design —
// the refinement has already run to a conclusion (in progress or already
// used), so retrying with the same source version cannot succeed.
export const REFINEMENT_SUBMIT_CODES_REQUIRING_RECHECK: ReadonlySet<string> = new Set([
  "design_not_refinable",
  "refinement_in_progress",
  "refinement_limit_reached",
]);

export function refinementSubmitErrorMessage(code: string, fallback: string): string {
  return REFINEMENT_SUBMIT_MESSAGES[code] ?? fallback;
}
