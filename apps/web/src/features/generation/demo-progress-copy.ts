// Pure, deterministic copy for a demo job (job.is_demo) on the shared
// generation-progress screen (Phase 15 §36). Keeps the normal stage
// meaning (queued/running_text/running_image) while using honest wording —
// never "contacting Claude", "generating with FLUX", "Replicate is
// rendering" or "newly generating your image", and never a fake percentage.

import type { GenerationJob } from "@/lib/api";

export function demoProgressHeading(
  status: GenerationJob["status"],
  isRefinement: boolean,
): string {
  switch (status) {
    case "queued":
      return isRefinement ? "Preparing your demo refinement" : "Preparing your demo concept";
    case "running_text":
      return isRefinement
        ? "Updating your deterministic design brief"
        : "Building your deterministic design brief";
    case "running_image":
      return isRefinement
        ? "Selecting and processing your refined demo visual"
        : "Selecting and processing your demo visual";
    default:
      return "Preparing your demo concept";
  }
}

export function demoProgressExplanation(
  status: GenerationJob["status"],
  isRefinement: boolean,
): string {
  switch (status) {
    case "queued":
      return "Your demo job is waiting to start.";
    case "running_text":
      return isRefinement
        ? "Only your selected change is being applied to a fresh copy of your deterministic design brief."
        : "Your selections are being converted into a deterministic design brief — no paid AI provider is contacted.";
    case "running_image":
      return "A pre-generated concept image is being selected from Sitara's curated demo pack and processed for private delivery.";
    default:
      return "";
  }
}
