import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { buildStepZodSchema, createStepResolver, validateAnswers } from "./validation";
import type { AnswerValue, Answers, QuestionnaireSchema, Step } from "./types";

// Load the SHARED cross-language contract from the repository root, walking up
// so it resolves both in CI (repo checkout) and locally (cwd = apps/web).
function loadContract(): {
  schema: QuestionnaireSchema;
  cases: {
    name: string;
    require_complete: boolean;
    answers: unknown;
    valid: boolean;
    error_questions?: string[];
  }[];
} {
  let dir = process.cwd();
  for (let i = 0; i < 8; i += 1) {
    try {
      const raw = readFileSync(
        join(dir, "contracts", "questionnaire-validation-cases.json"),
        "utf8",
      );
      return JSON.parse(raw);
    } catch {
      dir = dirname(dir);
    }
  }
  throw new Error("Shared questionnaire validation contract not found.");
}

const contract = loadContract();

describe("shared cross-language validation contract", () => {
  for (const testCase of contract.cases) {
    it(testCase.name, () => {
      const result = validateAnswers(
        contract.schema,
        testCase.answers,
        testCase.require_complete,
      );
      expect(result.valid).toBe(testCase.valid);
      for (const key of testCase.error_questions ?? []) {
        expect(Object.keys(result.errors)).toContain(key);
      }
    });
  }
});

describe("Zod validation is derived from the schema", () => {
  // Two schemas identical except for colour_palette.max_items: changing the
  // machine constraint changes the built Zod validation with NO code change.
  function schemaWithColourMax(max: number): QuestionnaireSchema {
    return {
      schema_version: 1,
      key: "test",
      title: "Test",
      steps: [
        {
          id: "colours",
          title: "Colours",
          questions: [
            {
              id: "colour_palette",
              type: "multi_choice",
              label: "Colours",
              required: true,
              options: [
                { value: "red", label: "Red" },
                { value: "gold", label: "Gold" },
                { value: "green", label: "Green" },
              ],
              constraints: { min_items: 1, max_items: max },
            },
          ],
        },
      ],
      rules: [],
    };
  }

  it("rejects more items when max_items is lowered, with no code change", () => {
    const answers = { colour_palette: ["red", "gold"] };
    const looseSchema = schemaWithColourMax(2);
    const strictSchema = schemaWithColourMax(1);

    const loose = buildStepZodSchema(looseSchema, looseSchema.steps[0], answers);
    const strict = buildStepZodSchema(strictSchema, strictSchema.steps[0], answers);

    expect(loose.safeParse(answers).success).toBe(true);
    expect(strict.safeParse(answers).success).toBe(false);
  });

  function schemaWithColourMin(min: number): QuestionnaireSchema {
    const base = schemaWithColourMax(3);
    base.steps[0].questions[0].constraints = { min_items: min, max_items: 3 };
    return base;
  }

  it("14: raising min_items changes validation with no code change", () => {
    const answers = { colour_palette: ["red"] };
    const lenient = schemaWithColourMin(1);
    const strict = schemaWithColourMin(2);
    expect(
      buildStepZodSchema(lenient, lenient.steps[0], answers).safeParse(answers).success,
    ).toBe(true);
    expect(
      buildStepZodSchema(strict, strict.steps[0], answers).safeParse(answers).success,
    ).toBe(false);
  });
});

describe("required vs minimum are separate concerns", () => {
  // colour_palette is required (min 2); final_notes is OPTIONAL (min_length 5).
  const schema: QuestionnaireSchema = {
    schema_version: 1,
    key: "test",
    title: "Test",
    steps: [
      {
        id: "step",
        title: "Step",
        questions: [
          {
            id: "colour_palette",
            type: "multi_choice",
            label: "Colours",
            required: true,
            options: [
              { value: "red", label: "Red" },
              { value: "gold", label: "Gold" },
            ],
            constraints: { min_items: 2, max_items: 2 },
          },
          {
            id: "final_notes",
            type: "text",
            label: "Notes",
            required: false,
            constraints: { min_length: 5, max_length: 50 },
          },
        ],
      },
    ],
    rules: [],
  };
  const step = schema.steps[0];

  it("12: a missing required field fails the derived Zod schema", () => {
    const result = buildStepZodSchema(schema, step, {}).safeParse({});
    expect(result.success).toBe(false);
  });

  it("an optional question left empty is valid", () => {
    // colour_palette present & valid, final_notes absent → OK.
    const answers = { colour_palette: ["red", "gold"] };
    expect(buildStepZodSchema(schema, step, answers).safeParse(answers).success).toBe(true);
  });

  it("13: an optional supplied value still obeys its minimum constraint", () => {
    const answers = { colour_palette: ["red", "gold"], final_notes: "hi" };
    const result = buildStepZodSchema(schema, step, answers).safeParse(answers);
    expect(result.success).toBe(false); // final_notes shorter than min_length 5
  });
});

describe("createStepResolver", () => {
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
            options: [{ value: "lehenga", label: "Lehenga" }],
          },
        ],
      },
    ],
    rules: [],
  };

  async function runResolver(step: Step | null, answers: Answers, values: Record<string, AnswerValue>) {
    const resolver = createStepResolver(() => ({ schema, step, answers }));
    return resolver(values, undefined, {
      fields: {},
      shouldUseNativeValidation: false,
    } as never);
  }

  it("11: the RHF resolver validates the current step through the derived Zod schema", async () => {
    const result = await runResolver(schema.steps[0], {}, {});
    expect(Object.keys(result.errors)).toContain("garment_type");
  });

  it("passes a valid step and errors nothing", async () => {
    const answers = { garment_type: "lehenga" };
    const result = await runResolver(schema.steps[0], answers, answers);
    expect(result.errors).toEqual({});
  });

  it("returns no errors when there is no step (e.g. the inspiration step)", async () => {
    const result = await runResolver(null, {}, {});
    expect(result.errors).toEqual({});
  });
});

describe("validateAnswers text normalisation", () => {
  const schema: QuestionnaireSchema = {
    schema_version: 1,
    key: "test",
    title: "Test",
    steps: [
      {
        id: "notes",
        title: "Notes",
        questions: [
          {
            id: "final_notes",
            type: "text",
            label: "Notes",
            required: false,
            constraints: { min_length: 0, max_length: 50 },
          },
        ],
      },
    ],
    rules: [],
  };

  it("normalises CRLF/CR to LF and trims outer whitespace", () => {
    const result = validateAnswers(schema, { final_notes: "  a\r\nb\rc  " }, false);
    expect(result.valid).toBe(true);
  });
});
