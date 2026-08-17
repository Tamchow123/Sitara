import { describe, expect, it } from "vitest";

import {
  isRefinementBudgetSpent,
  isRefinementEligible,
  isRefinementFailed,
  isRefinementRunning,
  isSupersededVersion,
  refinedVersionId,
} from "./refinement-eligibility";
import type { DesignDraft, DesignResult } from "@/lib/api";

type Job = NonNullable<DesignDraft["latest_job"]>;

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: "j1",
    design_id: "d1",
    design_version_id: "v1",
    status: "succeeded",
    error_code: null,
    generation_kind: "refinement",
    is_demo: false,
    created_at: "t",
    updated_at: "t",
    started_at: "t",
    completed_at: "t",
    ...overrides,
  } as Job;
}

function result(
  overrides: Partial<Pick<DesignResult, "design_version_id" | "refinements_remaining">> = {},
) {
  return { design_version_id: "v1", refinements_remaining: 3, ...overrides };
}

describe("isRefinementEligible — the budget", () => {
  it("offers a refinement on an untouched concept", () => {
    expect(isRefinementEligible(result(), { latest_job: null }, true)).toBe(true);
  });

  it("refuses once the server reports no rounds left", () => {
    // The budget comes from the server, never from counting jobs. This design
    // has a succeeded refinement that produced THIS version — the state the
    // old rule would have read as eligible — and zero is what closes it.
    const design = { latest_job: job({ design_version_id: "v1" }) };
    expect(isRefinementEligible(result({ refinements_remaining: 0 }), design, true)).toBe(false);
    expect(isRefinementBudgetSpent(result({ refinements_remaining: 0 }))).toBe(true);
  });

  it("still offers a refinement after one succeeded, when rounds remain", () => {
    // The behaviour the raised budget exists for. Under the old rule a
    // succeeded refinement job ended eligibility outright.
    const design = { latest_job: job({ design_version_id: "v2" }) };
    expect(
      isRefinementEligible(
        result({ design_version_id: "v2", refinements_remaining: 2 }),
        design,
        true,
      ),
    ).toBe(true);
  });

  it("never offers one while generation is unavailable, whatever the budget says", () => {
    expect(isRefinementEligible(result(), { latest_job: null }, false)).toBe(false);
  });

  it("refuses a negative count as firmly as zero", () => {
    // Not reachable from the server, which clamps at zero — asserted because
    // `> 0` and `!== 0` differ here and only one of them is safe.
    expect(isRefinementEligible(result({ refinements_remaining: -1 }), { latest_job: null }, true)).toBe(
      false,
    );
    expect(isRefinementBudgetSpent(result({ refinements_remaining: -1 }))).toBe(true);
  });
});

describe("isSupersededVersion — the chain", () => {
  it("marks a version superseded when the succeeded refinement produced another one", () => {
    const design = { latest_job: job({ design_version_id: "v2" }) };
    expect(isSupersededVersion(result({ design_version_id: "v1" }), design)).toBe(true);
    // And that alone withholds the form, even with rounds left: the server
    // refuses a source that is not the design's latest.
    expect(isRefinementEligible(result({ design_version_id: "v1" }), design, true)).toBe(false);
  });

  it("does not mark the version the refinement actually produced", () => {
    const design = { latest_job: job({ design_version_id: "v2" }) };
    expect(isSupersededVersion(result({ design_version_id: "v2" }), design)).toBe(false);
  });

  it("does not infer supersession from an unfinished or failed refinement", () => {
    // No child version exists yet, so nothing has been superseded. Running is
    // withheld for its own reason; a resolved failure is eligible again.
    const running = { latest_job: job({ status: "running_image", design_version_id: null }) };
    expect(isSupersededVersion(result(), running)).toBe(false);
    expect(isRefinementEligible(result(), running, true)).toBe(false);

    const failed = {
      latest_job: job({ status: "failed", error_code: "refinement_no_change", design_version_id: null }),
    };
    expect(isSupersededVersion(result(), failed)).toBe(false);
    expect(isRefinementEligible(result(), failed, true)).toBe(true);
  });

  it("ignores an initial generation entirely", () => {
    const design = { latest_job: job({ generation_kind: "initial", design_version_id: "v1" }) };
    expect(isSupersededVersion(result({ design_version_id: "v1" }), design)).toBe(false);
    expect(isRefinementEligible(result({ design_version_id: "v1" }), design, true)).toBe(true);
  });
});

describe("running, failed and the produced version", () => {
  it("reports a running refinement and withholds the form", () => {
    const design = { latest_job: job({ status: "running_image", design_version_id: null }) };
    expect(isRefinementRunning(design)).toBe(true);
    expect(isRefinementEligible(result(), design, true)).toBe(false);
  });

  it("reports a failed refinement but still allows a retry", () => {
    const design = {
      latest_job: job({ status: "failed", error_code: "refinement_no_change", design_version_id: null }),
    };
    expect(isRefinementFailed(design)).toBe(true);
    expect(isRefinementEligible(result(), design, true)).toBe(true);
  });

  it("names the produced version only for a succeeded refinement", () => {
    expect(refinedVersionId({ latest_job: job({ design_version_id: "v2" }) })).toBe("v2");
    expect(refinedVersionId({ latest_job: job({ status: "running_image" }) })).toBeNull();
    expect(refinedVersionId({ latest_job: job({ generation_kind: "initial" }) })).toBeNull();
    expect(refinedVersionId({ latest_job: null })).toBeNull();
  });

  it("claims nothing at all when the design is not known yet", () => {
    // undefined is a pending fetch, not "no job" — every predicate must stay
    // silent rather than assert a state the server has not reported.
    expect(isRefinementRunning(undefined)).toBe(false);
    expect(isRefinementFailed(undefined)).toBe(false);
    expect(isSupersededVersion(result(), undefined)).toBe(false);
    expect(refinedVersionId(undefined)).toBeNull();
  });
});
