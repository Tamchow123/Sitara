import { describe, expect, it } from "vitest";

import {
  REFINEMENT_PROGRESS_NOTES,
  refinementProgressExplanation,
  refinementProgressHeading,
} from "./refinement-progress-copy";

describe("refinementProgressHeading", () => {
  it("renders the refinement-specific queued heading", () => {
    expect(refinementProgressHeading("queued")).toBe("Preparing your refinement");
  });

  it("renders the refinement-specific running_text heading", () => {
    expect(refinementProgressHeading("running_text")).toBe("Updating your design brief");
  });

  it("renders the refinement-specific running_image heading", () => {
    expect(refinementProgressHeading("running_image")).toBe("Creating your refined visual concept");
  });
});

describe("refinementProgressExplanation", () => {
  it("never mentions a percentage, provider name, seed or storage stage", () => {
    for (const status of ["queued", "running_text", "running_image"] as const) {
      const text = refinementProgressExplanation(status);
      expect(text).not.toMatch(/%|anthropic|replicate|flux|seed|storage/i);
    }
  });
});

describe("REFINEMENT_PROGRESS_NOTES", () => {
  it("discloses that only the selected change is applied, the image is fresh, and the original stays available", () => {
    const joined = REFINEMENT_PROGRESS_NOTES.join(" ").toLowerCase();
    expect(joined).toMatch(/selected change/);
    expect(joined).toMatch(/fresh/);
    expect(joined).toMatch(/original/);
  });
});
