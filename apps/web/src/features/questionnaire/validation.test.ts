import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { buildStepZodSchema, validateAnswers } from "./validation";
import type { QuestionnaireSchema } from "./types";

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
