import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { requiredQuestions, visibleQuestions } from "./rules";
import {
  buildScreenPlan,
  firstUnansweredScreenIndex,
  hasRequiredQuestion,
  INSPIRATION_SCREEN_ID,
  isInspirationScreen,
  isScreenAnswered,
  resumeScreenIndex,
  screenIndexForQuestion,
} from "./screens";
import type { Answers, QuestionnaireSchema } from "./types";

const SCHEMA: QuestionnaireSchema = {
  schema_version: 1,
  key: "t",
  title: "T",
  steps: [
    {
      id: "opening",
      title: "Your garment and occasion",
      questions: [
        {
          id: "ceremony",
          type: "single_choice",
          label: "Which ceremony?",
          required: true,
          options: [{ value: "nikah", label: "Nikah" }],
        },
        {
          id: "garment_type",
          type: "single_choice",
          label: "Which garment?",
          required: true,
          options: [
            { value: "lehenga", label: "Lehenga" },
            { value: "saree", label: "Saree" },
          ],
        },
      ],
    },
    {
      id: "colours",
      title: "Which colours are yours?",
      description: "Pick as few or as many as you like.",
      questions: [
        {
          id: "fabric_colour",
          type: "colour_choice",
          label: "The fabric",
          required: false,
          options: [{ value: "scarlet", label: "Scarlet" }],
        },
        {
          id: "embroidery_colour",
          type: "colour_choice",
          label: "The embroidery",
          required: false,
          options: [{ value: "gold", label: "Gold" }],
        },
        { id: "custom_colours", type: "colour_list", label: "Your own colours", required: false },
      ],
    },
    {
      id: "the_drape",
      title: "The drape",
      questions: [
        {
          id: "dupatta_style",
          type: "single_choice",
          label: "How should the dupatta be styled?",
          required: false,
          options: [{ value: "front_drape", label: "Front drape" }],
        },
        {
          id: "saree_drape",
          type: "single_choice",
          label: "How should the saree be draped?",
          required: false,
          options: [{ value: "nivi_drape", label: "Nivi" }],
        },
      ],
    },
  ],
  rules: [
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
  ],
};

function plan(answers: Answers) {
  return buildScreenPlan(SCHEMA, answers);
}

function required(answers: Answers) {
  return requiredQuestions(SCHEMA, answers, visibleQuestions(SCHEMA, answers));
}

describe("buildScreenPlan", () => {
  it("gives every visible question its own screen, titled by the question", () => {
    const { screens } = plan({ garment_type: "lehenga" });
    expect(screens.slice(0, 2).map((s) => s.title)).toEqual([
      "Which ceremony?",
      "Which garment?",
    ]);
    expect(screens[0].questions).toHaveLength(1);
    // The heading IS the question, so the field must not print the label
    // again beneath it.
    expect(screens[0].titleIsQuestionLabel).toBe(true);
  });

  it("does not claim the heading is the question label on a grouped screen", () => {
    // A grouped screen is titled by the STEP, so each field still needs its
    // own visible label — suppressing them would leave the questions unnamed.
    const { screens } = plan({ garment_type: "lehenga" });
    const colours = screens.find((s) => s.categoryId === "colours");
    expect(colours?.titleIsQuestionLabel).toBe(false);
    expect(screens.at(-1)?.titleIsQuestionLabel).toBe(false); // inspiration
  });

  it("collapses a step's colour questions into one screen titled by the step", () => {
    const { screens } = plan({ garment_type: "lehenga" });
    const colours = screens.find((s) => s.categoryId === "colours");
    expect(colours?.title).toBe("Which colours are yours?");
    expect(colours?.description).toBe("Pick as few or as many as you like.");
    expect(colours?.questions.map((q) => q.id)).toEqual([
      "fabric_colour",
      "embroidery_colour",
      "custom_colours",
    ]);
    // ...and it is the ONLY screen that step contributes.
    expect(screens.filter((s) => s.categoryId === "colours")).toHaveLength(1);
  });

  it("keeps categories aligned with the schema's steps and adds inspiration last", () => {
    const { categories, screens } = plan({ garment_type: "lehenga" });
    expect(categories.map((c) => c.id)).toEqual([
      "opening",
      "colours",
      "the_drape",
      "__inspiration__",
    ]);
    // Each category points at its own first screen.
    for (const category of categories) {
      expect(screens[category.firstScreen].categoryId).toBe(category.id);
    }
    expect(isInspirationScreen(screens.at(-1))).toBe(true);
    expect(screens.at(-1)?.id).toBe(INSPIRATION_SCREEN_ID);
  });

  it("drops the screen for a question the rules have hidden", () => {
    const forLehenga = plan({ garment_type: "lehenga" }).screens.map((s) => s.id);
    const forSaree = plan({ garment_type: "saree" }).screens.map((s) => s.id);

    expect(forLehenga).toContain("dupatta_style");
    expect(forLehenga).not.toContain("saree_drape");
    expect(forSaree).toContain("saree_drape");
    expect(forSaree).not.toContain("dupatta_style");
    // The drape category survives either way — one question replaces the other.
    expect(plan({ garment_type: "saree" }).categories.map((c) => c.id)).toContain("the_drape");
  });

  it("numbers each screen within its category for the kicker", () => {
    const { screens } = plan({ garment_type: "lehenga" });
    const opening = screens.filter((s) => s.categoryId === "opening");
    expect(opening.map((s) => [s.positionInCategory, s.screensInCategory])).toEqual([
      [1, 2],
      [2, 2],
    ]);
    const drape = screens.filter((s) => s.categoryId === "the_drape");
    expect(drape.map((s) => [s.positionInCategory, s.screensInCategory])).toEqual([[1, 1]]);
  });

  it("omits a category whose every question is hidden", () => {
    const schema: QuestionnaireSchema = {
      ...SCHEMA,
      steps: [
        SCHEMA.steps[0],
        { id: "empty", title: "Empty", questions: [SCHEMA.steps[2].questions[1]] },
      ],
      rules: SCHEMA.rules,
    };
    // garment=lehenga hides saree_drape, leaving the step with nothing.
    const built = buildScreenPlan(schema, { garment_type: "lehenga" });
    expect(built.categories.map((c) => c.id)).toEqual(["opening", "__inspiration__"]);
  });
});

describe("screen completeness", () => {
  it("is answered only when every required question on it has a value", () => {
    const { screens } = plan({});
    const ceremony = screens[0];
    expect(isScreenAnswered(ceremony, {}, required({}))).toBe(false);
    expect(isScreenAnswered(ceremony, { ceremony: "nikah" }, required({}))).toBe(true);
  });

  it("treats an optional screen as answered even when empty", () => {
    const answers = { garment_type: "lehenga" };
    const colours = plan(answers).screens.find((s) => s.categoryId === "colours");
    expect(isScreenAnswered(colours!, answers, required(answers))).toBe(true);
    expect(hasRequiredQuestion(colours!, required(answers))).toBe(false);
  });
});

describe("firstUnansweredScreenIndex", () => {
  it("reports the first screen still needing a required answer", () => {
    const answers = { ceremony: "nikah" };
    expect(firstUnansweredScreenIndex(plan(answers), answers, required(answers))).toBe(1);
  });

  it("returns -1 — not a screen index — once nothing is outstanding", () => {
    // The distinction resumeScreenIndex cannot make: it would return the
    // inspiration screen's index here, which as a navigation ceiling would be
    // indistinguishable from "stop at the inspiration screen".
    const answers = { ceremony: "nikah", garment_type: "lehenga" };
    expect(firstUnansweredScreenIndex(plan(answers), answers, required(answers))).toBe(-1);
  });

  it("does not treat the inspiration screen itself as outstanding", () => {
    const answers = { ceremony: "nikah", garment_type: "lehenga" };
    const built = plan(answers);
    expect(isInspirationScreen(built.screens.at(-1)!)).toBe(true);
    expect(firstUnansweredScreenIndex(built, answers, required(answers))).toBe(-1);
  });

  it("reopens a gap behind the user when a later change clears an answer", () => {
    // The reported defect: an answer can go missing on a screen already passed,
    // and the ceiling has to fall back to it rather than staying at the end.
    const complete = { ceremony: "nikah", garment_type: "lehenga" };
    expect(firstUnansweredScreenIndex(plan(complete), complete, required(complete))).toBe(-1);
    const cleared: Answers = { ceremony: "nikah" };
    expect(firstUnansweredScreenIndex(plan(cleared), cleared, required(cleared))).toBe(1);
  });
});

describe("resumeScreenIndex", () => {
  it("lands on the first unanswered required screen, not the top of its category", () => {
    const answers = { ceremony: "nikah" };
    expect(resumeScreenIndex(plan(answers), answers, required(answers))).toBe(1);
  });

  it("lands on the inspiration screen once nothing required is outstanding", () => {
    const answers = { ceremony: "nikah", garment_type: "lehenga" };
    const built = plan(answers);
    const index = resumeScreenIndex(built, answers, required(answers));
    expect(isInspirationScreen(built.screens[index])).toBe(true);
  });
});

describe("screenIndexForQuestion", () => {
  it("finds the screen a question is rendered on, including a grouped one", () => {
    const built = plan({ garment_type: "lehenga" });
    expect(built.screens[screenIndexForQuestion(built, "garment_type")!].id).toBe("garment_type");
    // A colour question resolves to the shared colour screen.
    expect(built.screens[screenIndexForQuestion(built, "embroidery_colour")!].id).toBe(
      "fabric_colour",
    );
  });

  it("returns null for a question that is not currently shown", () => {
    const built = plan({ garment_type: "saree" });
    expect(screenIndexForQuestion(built, "dupatta_style")).toBeNull();
    expect(screenIndexForQuestion(built, "no_such_question")).toBeNull();
  });
});

// The shape the handoff actually specifies, checked against the REAL active
// schema rather than a fixture of this test's own making: 16 single-question
// screens across 9 progress categories. Read with fs (not an import) so the
// frontend build never takes a dependency on the backend tree — if the file
// moves, this test fails loudly, which is the point.
describe("the shipped questionnaire v4 schema", () => {
  const FIXTURE = path.join(
    path.dirname(fileURLToPath(import.meta.url)),
    "../../../../api/sitara/questionnaire/fixtures/questionnaire_v4.json",
  );

  function v4(): QuestionnaireSchema {
    const rows = JSON.parse(readFileSync(FIXTURE, "utf-8")) as {
      fields: { schema: QuestionnaireSchema };
    }[];
    return rows[0].fields.schema;
  }

  it("produces 16 screens across 9 categories for a lehenga brief", () => {
    const answers: Answers = { ceremony: "nikah", garment_type: "lehenga" };
    const built = buildScreenPlan(v4(), answers);

    expect(built.screens).toHaveLength(16);
    expect(built.categories).toHaveLength(9);
    expect(built.categories.at(-1)?.id).toBe("__inspiration__");
    // Every screen but the inspiration one asks a single question — except the
    // colour screen, which is the documented exception.
    const multi = built.screens.filter((s) => s.questions.length > 1);
    expect(multi.map((s) => s.categoryId)).toEqual(["colours"]);
  });

  it("stays at 16 screens for a saree brief, with the drape question swapped in", () => {
    const answers: Answers = { ceremony: "nikah", garment_type: "saree" };
    const built = buildScreenPlan(v4(), answers);

    expect(built.screens).toHaveLength(16);
    expect(built.screens.map((s) => s.id)).toContain("saree_drape");
    expect(built.screens.map((s) => s.id)).not.toContain("dupatta_style");
  });
});
