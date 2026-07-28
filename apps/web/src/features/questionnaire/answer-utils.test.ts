import { describe, expect, it } from "vitest";

import { clearStaleAnswers } from "./answer-utils";
import type { Answers, QuestionnaireSchema } from "./types";

// A schema with the v4 colour shape: one colour_choice that accepts the
// design's own colours, and the single colour_list that IS those colours. The
// hide rule exists so the palette can be made to disappear the way a published
// schema legitimately could (nothing in schema validation forbids a show/hide
// rule targeting a colour_list question).
const SCHEMA: QuestionnaireSchema = {
  schema_version: 1,
  key: "colours",
  title: "Colours",
  steps: [
    {
      id: "colours",
      title: "Colours",
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
        {
          id: "dupatta_colour",
          type: "colour_choice",
          label: "The dupatta",
          required: false,
          options: [
            { value: "scarlet", label: "Scarlet", visual_key: "colour_scarlet", group: "reds_maroons" },
          ],
          constraints: { allow_custom: true },
        },
        {
          id: "custom_colours",
          type: "colour_list",
          label: "Your own colours",
          required: false,
          constraints: { max_items: 8 },
        },
      ],
    },
  ],
  rules: [
    {
      id: "saree_hides_the_palette",
      when: { question_id: "garment_type", operator: "equals", values: ["saree"] },
      then: { action: "hide", question_id: "custom_colours" },
    },
  ],
};

const clean = (answers: Answers) => clearStaleAnswers(SCHEMA, answers);

describe("clearStaleAnswers and the design's own colours", () => {
  it("keeps the palette across an unrelated answer change", () => {
    // It is an array, not a string: before schema v4 it fell through to the
    // text branch and was dropped on EVERY answer change.
    const result = clean({ garment_type: "lehenga", custom_colours: ["#7f2b4a"] });
    expect(result.custom_colours).toEqual(["#7f2b4a"]);
  });

  it("keeps a declared colour and a custom colour the palette still carries", () => {
    expect(clean({ garment_type: "lehenga", dupatta_colour: "scarlet" }).dupatta_colour).toBe(
      "scarlet",
    );
    const withCustom = clean({
      garment_type: "lehenga",
      custom_colours: ["#7f2b4a"],
      dupatta_colour: "#7f2b4a",
    });
    // A custom hex is legitimately absent from the question's declared options,
    // so it must not be filtered against the allow-set.
    expect(withCustom.dupatta_colour).toBe("#7f2b4a");
  });

  it("drops a custom colour choice when the palette no longer carries it", () => {
    const result = clean({
      garment_type: "lehenga",
      custom_colours: ["#111111"],
      dupatta_colour: "#7f2b4a",
    });
    expect(result.dupatta_colour).toBeUndefined();
    expect(result.custom_colours).toEqual(["#111111"]);
  });

  it("drops both the palette and its references when the palette is hidden", () => {
    // Otherwise the next save would carry a hex with no palette to justify it,
    // and the backend would reject the whole request.
    const result = clean({
      garment_type: "saree",
      custom_colours: ["#7f2b4a"],
      dupatta_colour: "#7f2b4a",
    });
    expect(result.custom_colours).toBeUndefined();
    expect(result.dupatta_colour).toBeUndefined();
  });

  it("keeps a declared colour even when the palette is hidden", () => {
    const result = clean({
      garment_type: "saree",
      custom_colours: ["#7f2b4a"],
      dupatta_colour: "scarlet",
    });
    expect(result.dupatta_colour).toBe("scarlet");
  });
});

describe("clearStaleAnswers is total over stored answers", () => {
  it("fails closed on a malformed palette instead of throwing", () => {
    // It runs on EVERY answer change, so a bad stored value must degrade, not
    // break the wizard for the rest of the session.
    const call = () =>
      clean({
        garment_type: "lehenga",
        custom_colours: [42 as unknown as string, "#7f2b4a"],
        dupatta_colour: "#7f2b4a",
      });
    expect(call).not.toThrow();
    const result = call();
    // Palette unusable -> the custom colour that depended on it goes too. The
    // malformed value itself is left for validation to report on its own
    // question rather than being silently repaired here.
    expect(result.dupatta_colour).toBeUndefined();
  });
});
