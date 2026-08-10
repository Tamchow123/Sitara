// Shared friendly copy for the SYNCHRONOUS submit-time failures of both
// POST /designs/{id}/generate/ and /refine/ (returned before any job exists,
// so distinct from generation-errors.ts, which covers a job's terminal
// error_code surfaced later on the progress route).
//
// The Phase 16 admission codes each get plain, non-technical copy. It never
// mentions budgets, pricing, rate-limit internals, providers, models or Redis,
// never displays an internal amount, and never invites an automatic retry — the
// copy points the user to "later" and reassures them their design is saved.

// The Phase 21 sign-in gate. Kept as a named constant because two callers must
// recognise it as something other than a failure: it is the one submit-time
// refusal the user can act on immediately, in a different place (sign in or
// register), and the copy must say so without implying the answers are lost.
export const GENERATION_SIGN_IN_REQUIRED_CODE = "authentication_required";

export const GENERATION_SUBMIT_MESSAGES: Record<string, string> = {
  [GENERATION_SIGN_IN_REQUIRED_CODE]:
    "Please sign in or create an account to generate your concept. Your answers are saved.",
  live_generation_disabled:
    "Live concept generation is currently turned off. Your design has been saved.",
  generation_limit_reached:
    "You've reached the limit for generating concepts for now. Your design is saved — please try again later.",
  live_generation_budget_exhausted:
    "The daily limit for generating new concepts has been reached. Your design is saved — please try again later.",
  generation_unavailable: "Concept generation is not available right now. Please try again shortly.",
  queue_unavailable:
    "The generation queue is temporarily unavailable. Please try again shortly.",
};

// Terminal-for-now admission codes: retrying immediately cannot succeed, so a
// consumer should not offer an instant retry or poll — the user comes back later.
// The sign-in gate belongs here too, for the opposite reason: pressing the same
// button again while still signed out can only produce the same refusal. A
// consumer that can route to sign-in should do that INSTEAD of an error block
// (see ReviewSummary); one that cannot at least stops offering a useless retry.
export const GENERATION_SUBMIT_TERMINAL_CODES: ReadonlySet<string> = new Set([
  GENERATION_SIGN_IN_REQUIRED_CODE,
  "live_generation_disabled",
  "generation_limit_reached",
  "live_generation_budget_exhausted",
]);

export function generationSubmitErrorMessage(code: string, fallback: string): string {
  return GENERATION_SUBMIT_MESSAGES[code] ?? fallback;
}
