// Pure, deterministic copy for the refinement branch of the shared
// generation-progress screen (Phase 14 §29). No component of its own — the
// only consumer is GenerationProgress.tsx in this same folder, which renders
// these when job.generation_kind === "refinement" while reusing the same
// polling/backoff/redirect machinery for both kinds. Kept inside
// features/generation/ (not features/refinement/) so the shared,
// generation_kind-agnostic progress screen never has to import from a more
// specific downstream feature — refinement/ depends on generation/, not the
// other way round.

import type { GenerationJob } from "@/lib/api";

export function refinementProgressHeading(status: GenerationJob["status"]): string {
  switch (status) {
    case "queued":
      return "Preparing your refinement";
    case "running_text":
      return "Updating your design brief";
    case "running_image":
      return "Creating your refined visual concept";
    default:
      return "Preparing your refinement";
  }
}

export function refinementProgressExplanation(status: GenerationJob["status"]): string {
  switch (status) {
    case "queued":
      return "Your refinement job is waiting to start.";
    case "running_text":
      return "Only your selected change is being applied to a fresh copy of your design brief.";
    case "running_image":
      return "Your refined image is being generated, verified and stored privately.";
    default:
      return "";
  }
}

export const REFINEMENT_PROGRESS_NOTES: readonly string[] = [
  "Only your selected change is being requested.",
  "The result is still a fresh generation, not an edit of your original image.",
  "Your original concept remains private and available.",
];
