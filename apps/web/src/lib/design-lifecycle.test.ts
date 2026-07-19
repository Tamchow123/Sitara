import { describe, expect, it } from "vitest";

import { resolveDesignLifecycleTarget } from "./design-lifecycle";
import type { DesignDraft } from "@/features/questionnaire/types";
import type { GenerationJob } from "@/lib/api";

function design(overrides: Partial<DesignDraft> = {}): DesignDraft {
  return {
    id: "d1",
    title: "My concept",
    status: "draft",
    questionnaire: null,
    answers: {},
    selected_inspirations: [],
    latest_job: null,
    created_at: "t",
    updated_at: "t",
    ...overrides,
  };
}

function job(overrides: Partial<GenerationJob> = {}): GenerationJob {
  return {
    id: "j1",
    design_id: "d1",
    design_version_id: null,
    status: "queued",
    error_code: null,
    created_at: "t",
    updated_at: "t",
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

describe("resolveDesignLifecycleTarget", () => {
  it("routes draft to the questionnaire", () => {
    expect(resolveDesignLifecycleTarget(design({ status: "draft" }))).toEqual({
      kind: "questionnaire",
    });
  });

  it("routes generation_failed with no linked version to the questionnaire", () => {
    const target = resolveDesignLifecycleTarget(
      design({ status: "generation_failed", latest_job: job({ status: "failed" }) }),
    );
    expect(target).toEqual({ kind: "questionnaire" });
  });

  it("routes generation_failed with a linked version to the progress route", () => {
    const target = resolveDesignLifecycleTarget(
      design({
        status: "generation_failed",
        latest_job: job({ id: "j2", status: "failed", design_version_id: "v1" }),
      }),
    );
    expect(target).toEqual({ kind: "progress", href: "/design/d1/generation/j2" });
  });

  it("routes generating with a coherent in-progress job to the progress route", () => {
    const target = resolveDesignLifecycleTarget(
      design({ status: "generating", latest_job: job({ id: "j3", status: "running_image" }) }),
    );
    expect(target).toEqual({ kind: "progress", href: "/design/d1/generation/j3" });
  });

  it("treats generating with no job as inconsistent", () => {
    const target = resolveDesignLifecycleTarget(design({ status: "generating", latest_job: null }));
    expect(target).toEqual({ kind: "unavailable" });
  });

  it("treats generating with a stale succeeded job as inconsistent", () => {
    const target = resolveDesignLifecycleTarget(
      design({
        status: "generating",
        latest_job: job({ status: "succeeded", design_version_id: "v1" }),
      }),
    );
    expect(target).toEqual({ kind: "unavailable" });
  });

  it("routes generated with a succeeded job and version id to the result route", () => {
    const target = resolveDesignLifecycleTarget(
      design({
        status: "generated",
        latest_job: job({ id: "j4", status: "succeeded", design_version_id: "v9" }),
      }),
    );
    expect(target).toEqual({ kind: "result", href: "/design/d1/result/v9" });
  });

  it("treats generated with no succeeded job as inconsistent", () => {
    const target = resolveDesignLifecycleTarget(design({ status: "generated", latest_job: null }));
    expect(target).toEqual({ kind: "unavailable" });
  });

  it("treats generated with a succeeded job but no version id as inconsistent", () => {
    const target = resolveDesignLifecycleTarget(
      design({
        status: "generated",
        latest_job: job({ status: "succeeded", design_version_id: null }),
      }),
    );
    expect(target).toEqual({ kind: "unavailable" });
  });

  it("treats an unrecognised status as inconsistent", () => {
    const target = resolveDesignLifecycleTarget(
      design({ status: "something_unexpected" as DesignDraft["status"] }),
    );
    expect(target).toEqual({ kind: "unavailable" });
  });
});
