import { describe, expect, it } from "vitest";

import { clearStaleAnswers, resumeStepIndex } from "./answer-utils";
import { allowedOptions, requiredQuestions, visibleQuestions } from "./rules";
import type { QuestionnaireSchema } from "./types";

// Compact schema exercising show/hide/require/restrict — the SAME semantics
// the backend enforces, interpreted generically from the data.
const schema: QuestionnaireSchema = {
  schema_version: 1,
  key: "test",
  title: "Test",
  steps: [
    {
      id: "garment",
      title: "Garment",
      questions: [
        {
          id: "garment_type",
          type: "single_choice",
          label: "Garment",
          required: true,
          options: [
            { value: "lehenga", label: "Lehenga" },
            { value: "saree", label: "Saree" },
          ],
        },
      ],
    },
    {
      id: "detail",
      title: "Detail",
      questions: [
        {
          id: "silhouette",
          type: "single_choice",
          label: "Silhouette",
          required: true,
          options: [
            { value: "flared_lehenga", label: "Flared" },
            { value: "classic_saree_drape", label: "Draped" },
          ],
        },
        {
          id: "dupatta_style",
          type: "single_choice",
          label: "Dupatta",
          required: false,
          options: [{ value: "head_drape", label: "Head" }],
        },
        {
          id: "saree_drape",
          type: "single_choice",
          label: "Saree drape",
          required: false,
          options: [{ value: "nivi_drape", label: "Nivi" }],
        },
      ],
    },
  ],
  rules: [
    {
      id: "saree_shows_drape",
      when: { question_id: "garment_type", operator: "equals", values: ["saree"] },
      then: { action: "show", question_id: "saree_drape" },
    },
    {
      id: "saree_hides_dupatta",
      when: { question_id: "garment_type", operator: "equals", values: ["saree"] },
      then: { action: "hide", question_id: "dupatta_style" },
    },
    {
      id: "non_saree_hides_drape",
      when: { question_id: "garment_type", operator: "not_in", values: ["saree"] },
      then: { action: "hide", question_id: "saree_drape" },
    },
    {
      id: "lehenga_silhouette",
      when: { question_id: "garment_type", operator: "equals", values: ["lehenga"] },
      then: {
        action: "restrict_options",
        question_id: "silhouette",
        values: ["flared_lehenga"],
      },
    },
    {
      id: "saree_silhouette",
      when: { question_id: "garment_type", operator: "equals", values: ["saree"] },
      then: {
        action: "restrict_options",
        question_id: "silhouette",
        values: ["classic_saree_drape"],
      },
    },
  ],
};

describe("visibility", () => {
  it("hides show-targeted questions by default and reveals them on a match", () => {
    const empty = visibleQuestions(schema, {});
    expect(empty.saree_drape).toBe(false); // hidden by default (show-targeted)
    expect(empty.dupatta_style).toBe(true); // visible by default

    const saree = visibleQuestions(schema, { garment_type: "saree" });
    expect(saree.saree_drape).toBe(true);
    expect(saree.dupatta_style).toBe(false); // hidden for saree
  });
});

describe("restricted options", () => {
  it("intersects restrict_options to the garment-appropriate silhouettes", () => {
    expect([...allowedOptions(schema, { garment_type: "lehenga" }).silhouette]).toEqual([
      "flared_lehenga",
    ]);
    expect([...allowedOptions(schema, { garment_type: "saree" }).silhouette]).toEqual([
      "classic_saree_drape",
    ]);
  });
});

describe("required only while visible", () => {
  it("does not require a hidden question", () => {
    const required = requiredQuestions(
      schema,
      { garment_type: "saree" },
      visibleQuestions(schema, { garment_type: "saree" }),
    );
    expect(required.garment_type).toBe(true);
    // dupatta is hidden for saree → not required (it is optional anyway).
    expect(required.dupatta_style).toBe(false);
  });
});

describe("clearStaleAnswers", () => {
  it("drops answers that become hidden or disallowed when a controller changes", () => {
    const before = {
      garment_type: "lehenga",
      silhouette: "flared_lehenga",
      dupatta_style: "head_drape",
    };
    // Switch to saree: the lehenga silhouette is no longer allowed and dupatta
    // becomes hidden — both must be cleared.
    const after = clearStaleAnswers(schema, { ...before, garment_type: "saree" });
    expect(after.garment_type).toBe("saree");
    expect(after.silhouette).toBeUndefined();
    expect(after.dupatta_style).toBeUndefined();
  });
});

describe("none embellishment hides density", () => {
  // A self-contained schema mirroring the fixture rule
  // `none_hides_embellishment_density` (equals ["none"] → hide density).
  const embSchema: QuestionnaireSchema = {
    schema_version: 1,
    key: "emb",
    title: "Emb",
    steps: [
      {
        id: "embellishment",
        title: "Embellishment",
        questions: [
          {
            id: "embellishment_styles",
            type: "multi_choice",
            label: "Styles",
            required: false,
            options: [
              { value: "zardozi", label: "Zardozi" },
              { value: "none", label: "None" },
            ],
            constraints: { exclusive_values: ["none"] },
          },
          {
            id: "embellishment_density",
            type: "single_choice",
            label: "Density",
            required: false,
            options: [
              { value: "minimal", label: "Minimal" },
              { value: "heavy", label: "Heavy" },
            ],
          },
        ],
      },
    ],
    rules: [
      {
        id: "none_hides_embellishment_density",
        when: { question_id: "embellishment_styles", operator: "equals", values: ["none"] },
        then: { action: "hide", question_id: "embellishment_density" },
      },
    ],
  };

  it("keeps density visible for a normal style selection", () => {
    const visibility = visibleQuestions(embSchema, { embellishment_styles: ["zardozi"] });
    expect(visibility.embellishment_density).toBe(true);
  });

  it("clears a stale heavy density when styles change to none", () => {
    const before = { embellishment_styles: ["zardozi"], embellishment_density: "heavy" };
    // Switch to none: density becomes hidden and must be cleared.
    const after = clearStaleAnswers(embSchema, {
      ...before,
      embellishment_styles: ["none"],
    });
    expect(after.embellishment_styles).toEqual(["none"]);
    expect(after.embellishment_density).toBeUndefined();
  });
});

describe("resumeStepIndex", () => {
  it("returns the first incomplete step", () => {
    expect(resumeStepIndex(schema, {})).toBe(0);
    expect(resumeStepIndex(schema, { garment_type: "lehenga" })).toBe(1);
  });

  it("returns steps.length when every step is complete", () => {
    const complete = { garment_type: "lehenga", silhouette: "flared_lehenga" };
    expect(resumeStepIndex(schema, complete)).toBe(schema.steps.length);
  });
});
