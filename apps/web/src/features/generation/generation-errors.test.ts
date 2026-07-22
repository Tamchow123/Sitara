import { describe, expect, it } from "vitest";

import { friendlyGenerationError, type GenerationErrorCode } from "./generation-errors";

// Every stable backend error code (kept in sync manually here; the module
// under test itself fails the TypeScript build if its map ever misses one of
// these, via `satisfies Record<GenerationErrorCode, ...>`).
const ALL_CODES: GenerationErrorCode[] = [
  "queue_unavailable",
  "generation_unavailable",
  "design_incomplete",
  "design_changed",
  "structured_generation_failed",
  "structured_submission_ambiguous",
  "structured_provider_refused",
  "prompt_build_failed",
  "image_provider_unavailable",
  "image_submission_ambiguous",
  "image_prediction_failed",
  "image_prediction_canceled",
  "image_prediction_aborted",
  "image_poll_timeout",
  "image_download_failed",
  "image_output_invalid",
  "image_staging_failed",
  "image_staging_unverified",
  "image_ingest_failed",
  "image_ingest_unverified",
  "internal_generation_error",
  "live_generation_budget_exhausted",
];

const FORBIDDEN_WORDS = [
  "anthropic",
  "replicate",
  "claude",
  "flux",
  "black-forest",
  "prediction id",
  "storage key",
  "hash",
  "billing",
];

describe("friendlyGenerationError", () => {
  it("covers every stable backend error code with a non-empty heading and message", () => {
    for (const code of ALL_CODES) {
      const friendly = friendlyGenerationError(code);
      expect(friendly.heading.length).toBeGreaterThan(0);
      expect(friendly.message.length).toBeGreaterThan(0);
    }
  });

  it("never mentions provider names, model ids or internal terms", () => {
    for (const code of ALL_CODES) {
      const friendly = friendlyGenerationError(code);
      const text = `${friendly.heading} ${friendly.message}`.toLowerCase();
      for (const word of FORBIDDEN_WORDS) {
        expect(text).not.toContain(word);
      }
    }
  });

  it("marks design_incomplete and design_changed as editable questionnaire problems", () => {
    expect(friendlyGenerationError("design_incomplete").editable).toBe(true);
    expect(friendlyGenerationError("design_changed").editable).toBe(true);
  });

  it("marks technical failures (e.g. image_ingest_failed) as non-editable", () => {
    expect(friendlyGenerationError("image_ingest_failed").editable).toBe(false);
    expect(friendlyGenerationError("internal_generation_error").editable).toBe(false);
  });

  it("explains ambiguous-submission states without encouraging an automatic retry", () => {
    const structured = friendlyGenerationError("structured_submission_ambiguous");
    const image = friendlyGenerationError("image_submission_ambiguous");
    expect(structured.message.toLowerCase()).toContain("confirm");
    expect(image.message.toLowerCase()).toContain("confirm");
  });

  it("falls back to a safe generic message for an unrecognised code", () => {
    const friendly = friendlyGenerationError("some_future_code_not_yet_known");
    expect(friendly.heading.length).toBeGreaterThan(0);
    expect(friendly.message.length).toBeGreaterThan(0);
  });

  it("falls back to a safe generic message for a null code", () => {
    const friendly = friendlyGenerationError(null);
    expect(friendly.heading.length).toBeGreaterThan(0);
  });
});
