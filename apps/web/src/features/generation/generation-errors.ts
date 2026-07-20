// Exhaustive, source-controlled friendly-message map for every stable
// generation error code. Derived from the backend's own error taxonomy via
// the generated OpenAPI enum — `satisfies Record<...>` below makes the
// TypeScript compiler fail the build if the generated union ever gains a
// code this map does not cover.
//
// Messages are plain, user-facing language. They never mention Anthropic,
// Replicate, model IDs, predictions, storage keys, hashes or billing
// internals, and they distinguish editable questionnaire problems from
// technical failures.

import type { GenerationJob } from "@/lib/api";

export type GenerationErrorCode = NonNullable<GenerationJob["error_code"]>;

export type FriendlyGenerationError = {
  heading: string;
  message: string;
  // True when the recommended recovery is editing the questionnaire again
  // (a link back is meaningful); false for purely technical failures where
  // editing answers would not help.
  editable: boolean;
};

const GENERATION_ERROR_MESSAGES = {
  queue_unavailable: {
    heading: "Generation could not start",
    message:
      "The generation queue is temporarily unavailable. Please try again shortly.",
    editable: false,
  },
  generation_unavailable: {
    heading: "Generation is not currently available",
    message:
      "Concept generation is not available right now. Please try again later.",
    editable: false,
  },
  design_incomplete: {
    heading: "Your design isn't complete yet",
    message:
      "Some required questions still need answers before a concept can be generated.",
    editable: true,
  },
  design_changed: {
    heading: "Your design changed during generation",
    message:
      "The design was edited while generation was in progress, so it could not continue. Please review your answers and try again.",
    editable: true,
  },
  structured_generation_failed: {
    heading: "We couldn't create your design brief",
    message:
      "Something went wrong while creating the structured design brief. Please try again shortly.",
    editable: false,
  },
  structured_submission_ambiguous: {
    heading: "We couldn't confirm your design brief",
    message:
      "We could not confirm whether the design brief request went through. Please wait a moment before trying again, rather than resubmitting immediately.",
    editable: false,
  },
  structured_provider_refused: {
    heading: "We couldn't create your design brief",
    message:
      "The request could not be completed as described. Please review your answers and try again.",
    editable: true,
  },
  prompt_build_failed: {
    heading: "We couldn't prepare your visual concept",
    message: "Something went wrong while preparing the image request. Please try again shortly.",
    editable: false,
  },
  image_provider_unavailable: {
    heading: "We couldn't create your visual concept",
    message:
      "The image generation service is temporarily unavailable. Please try again shortly.",
    editable: false,
  },
  image_submission_ambiguous: {
    heading: "We couldn't confirm your visual concept request",
    message:
      "We could not confirm whether the image request went through. Please wait a moment before trying again, rather than resubmitting immediately.",
    editable: false,
  },
  image_prediction_failed: {
    heading: "We couldn't create your visual concept",
    message: "Image generation failed. Please try again shortly.",
    editable: false,
  },
  image_prediction_canceled: {
    heading: "Image generation was canceled",
    message: "The visual concept generation was canceled. Please try again.",
    editable: false,
  },
  image_prediction_aborted: {
    heading: "Image generation stopped unexpectedly",
    message: "The visual concept generation stopped unexpectedly. Please try again.",
    editable: false,
  },
  image_poll_timeout: {
    heading: "Image generation took too long",
    message: "Generating your visual concept took longer than expected. Please try again.",
    editable: false,
  },
  image_download_failed: {
    heading: "We couldn't retrieve your visual concept",
    message: "Something went wrong while retrieving the generated image. Please try again.",
    editable: false,
  },
  image_output_invalid: {
    heading: "We couldn't use the generated image",
    message: "The generated image could not be used. Please try again.",
    editable: false,
  },
  image_staging_failed: {
    heading: "We couldn't safely store your visual concept",
    message:
      "Something went wrong while safely preparing your image for storage. Please try again.",
    editable: false,
  },
  image_staging_unverified: {
    heading: "We couldn't confirm your visual concept was stored",
    message:
      "We could not confirm your image was safely prepared for storage. Please try again shortly.",
    editable: false,
  },
  image_ingest_unverified: {
    heading: "We couldn't confirm your visual concept was saved",
    message:
      "We could not confirm your image was safely and privately saved. Please try again shortly.",
    editable: false,
  },
  image_ingest_failed: {
    heading: "We couldn't safely save your visual concept",
    message:
      "Something went wrong while safely and privately saving your image. Please try again.",
    editable: false,
  },
  internal_generation_error: {
    heading: "Something went wrong",
    message: "An unexpected problem occurred while generating your concept. Please try again.",
    editable: false,
  },
  // Since Phase 14: a client-submitted refinement request is rejected as a
  // controlled 400 before any attempt is created, so this code is never
  // actually persisted onto a job — kept here only so the exhaustive map
  // compiles against the full backend error-code enum.
  refinement_invalid: {
    heading: "We couldn't start that refinement",
    message: "Please choose one change and check your note, then try again.",
    editable: false,
  },
  refinement_no_change: {
    heading: "No change was made",
    message:
      "We weren't able to produce an actual change for your selected category. Please try a different note or category.",
    editable: false,
  },
  refinement_generation_failed: {
    heading: "We couldn't refine your concept",
    message: "Something went wrong while updating your design brief. Please try again shortly.",
    editable: false,
  },
  refinement_limit_reached: {
    heading: "This design has already been refined",
    message: "Only one refinement is available per concept.",
    editable: false,
  },
  refinement_source_unavailable: {
    heading: "This concept can't be refined right now",
    message: "The original concept is not available for refinement. Please try again shortly.",
    editable: false,
  },
} satisfies Record<GenerationErrorCode, FriendlyGenerationError>;

// Runtime defence: an error code the frontend does not recognise (a future
// backend addition not yet reflected in the generated types) still renders a
// safe, generic message instead of crashing or showing nothing.
const UNKNOWN_ERROR: FriendlyGenerationError = {
  heading: "Something went wrong",
  message: "An unexpected problem occurred while generating your concept. Please try again.",
  editable: false,
};

export function friendlyGenerationError(code: string | null): FriendlyGenerationError {
  if (code === null) return UNKNOWN_ERROR;
  const known = (GENERATION_ERROR_MESSAGES as Record<string, FriendlyGenerationError>)[code];
  return known ?? UNKNOWN_ERROR;
}
