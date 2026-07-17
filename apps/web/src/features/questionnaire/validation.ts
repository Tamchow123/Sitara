// Frontend answer validation, DERIVED from the machine-readable schema — the
// mirror of the backend's sitara.questionnaire.answer_validation. Backend
// validation stays authoritative; this exists for immediate, accessible UX
// feedback and is exercised against the SAME shared cross-language contract.
//
// Two surfaces, both derived from the schema (never hard-coding a rule):
//  - validateAnswers(): total procedural validation used for the review and
//    the shared contract; returns per-question errors.
//  - buildStepZodSchema(): a per-step Zod schema built from the machine
//    constraints, used with react-hook-form for inline step validation, plus
//    a static Zod schema for the stable API request envelope.

import { z } from "zod";

import {
  allowedOptions,
  buildSelected,
  declaredOptionValues,
  questionsById,
  requiredQuestions,
  visibleQuestions,
} from "./rules";
import {
  TOP_LEVEL_ERROR_KEY,
  type AnswerValue,
  type Answers,
  type FieldErrors,
  type Question,
  type QuestionnaireSchema,
  type Step,
} from "./types";

const MSG = {
  unknownQuestion: "This question is not part of the questionnaire.",
  notApplicable: "This question does not apply to your current answers.",
  wrongSingle: "Choose one of the available options.",
  wrongMulti: "Select from the available options.",
  wrongText: "This answer must be text.",
  unknownOption: "That option is not available.",
  duplicate: "The same option was selected more than once.",
  tooFew: (n: number) => `Please select at least ${n}.`,
  tooMany: (n: number) => `Please select at most ${n}.`,
  exclusive: "That option cannot be combined with any other.",
  noOptions: "No options are available for your current answers.",
  tooShort: (n: number) => `Please use at least ${n} characters.`,
  tooLong: (n: number) => `Please use at most ${n} characters.`,
  required: "This question is required.",
};

export function normaliseText(value: string): string {
  // CRLF/CR → LF, trim OUTER whitespace, preserve internal whitespace.
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

type Structural = { value?: AnswerValue; selected?: Set<string>; message?: string };

function structuralValue(question: Question, value: unknown): Structural {
  const declared = new Set(declaredOptionValues(question));
  if (question.type === "single_choice") {
    if (typeof value !== "string") return { message: MSG.wrongSingle };
    if (!declared.has(value)) return { message: MSG.unknownOption };
    return { value, selected: new Set([value]) };
  }
  if (question.type === "multi_choice") {
    if (!Array.isArray(value)) return { message: MSG.wrongMulti };
    const seen: string[] = [];
    for (const item of value) {
      if (typeof item !== "string") return { message: MSG.wrongMulti };
      if (!declared.has(item)) return { message: MSG.unknownOption };
      if (seen.includes(item)) return { message: MSG.duplicate };
      seen.push(item);
    }
    return { value: seen, selected: new Set(seen) };
  }
  if (question.type === "text") {
    if (typeof value !== "string") return { message: MSG.wrongText };
    return { value: normaliseText(value) };
  }
  return { message: MSG.wrongText };
}

function isAnswered(question: Question, value: AnswerValue | undefined): boolean {
  if (question.type === "multi_choice") return Array.isArray(value) && value.length > 0;
  if (question.type === "text") return typeof value === "string" && value !== "";
  return typeof value === "string" && value !== "";
}

function checkMulti(
  values: string[],
  question: Question,
  requireComplete: boolean,
): string | null {
  const c = question.constraints ?? {};
  const exclusive = new Set(c.exclusive_values ?? []);
  if (exclusive.size > 0 && values.length > 1 && values.some((v) => exclusive.has(v))) {
    return MSG.exclusive;
  }
  if (typeof c.max_items === "number" && values.length > c.max_items) {
    return MSG.tooMany(c.max_items);
  }
  if (requireComplete && typeof c.min_items === "number" && values.length < c.min_items) {
    return MSG.tooFew(c.min_items);
  }
  return null;
}

function checkText(
  value: string,
  question: Question,
  requireComplete: boolean,
): string | null {
  const c = question.constraints ?? {};
  if (typeof c.max_length === "number" && value.length > c.max_length) {
    return MSG.tooLong(c.max_length);
  }
  if (requireComplete && typeof c.min_length === "number" && value.length < c.min_length) {
    return MSG.tooShort(c.min_length);
  }
  return null;
}

export type ValidationResult = { valid: boolean; errors: FieldErrors };

// Total over arbitrary input — a mirror of the authoritative backend rules.
export function validateAnswers(
  schema: QuestionnaireSchema,
  answers: unknown,
  requireComplete: boolean,
): ValidationResult {
  const index = questionsById(schema);
  if (!isPlainObject(answers)) {
    return { valid: false, errors: { [TOP_LEVEL_ERROR_KEY]: ["Answers must be an object."] } };
  }

  const errors: FieldErrors = {};
  const structural: Answers = {};
  for (const [key, value] of Object.entries(answers)) {
    const question = index[key];
    if (!question) {
      errors[key] = [MSG.unknownQuestion];
      continue;
    }
    const result = structuralValue(question, value);
    if (result.message) {
      errors[key] = [result.message];
      continue;
    }
    structural[key] = result.value as AnswerValue;
  }

  const visibility = visibleQuestions(schema, structural);
  const required = requiredQuestions(schema, structural, visibility);
  const allowed = allowedOptions(schema, structural);
  const selected = buildSelected(schema, structural);

  const normalised: Answers = {};
  for (const [key, value] of Object.entries(structural)) {
    const question = index[key];
    if (!visibility[key]) {
      errors[key] = [MSG.notApplicable];
      continue;
    }
    if (question.type === "single_choice" || question.type === "multi_choice") {
      const allow = allowed[key] ?? new Set<string>();
      if (allow.size === 0) {
        errors[key] = [MSG.noOptions];
        continue;
      }
      const chosen = selected[key] ?? new Set<string>();
      let disallowed = false;
      for (const v of chosen) if (!allow.has(v)) disallowed = true;
      if (disallowed) {
        errors[key] = [MSG.unknownOption];
        continue;
      }
    }
    if (question.type === "multi_choice") {
      const message = checkMulti(value as string[], question, requireComplete);
      if (message) {
        errors[key] = [message];
        continue;
      }
    } else if (question.type === "text") {
      const message = checkText(value as string, question, requireComplete);
      if (message) {
        errors[key] = [message];
        continue;
      }
    }
    normalised[key] = value;
  }

  if (requireComplete) {
    for (const [questionId, isRequired] of Object.entries(required)) {
      if (!isRequired || errors[questionId]) continue;
      if (!isAnswered(index[questionId], normalised[questionId])) {
        errors[questionId] = [MSG.required];
      }
    }
  }

  return { valid: Object.keys(errors).length === 0, errors };
}

// ---------------------------------------------------------------------------
// Zod: per-step schema derived from the machine constraints (used by RHF), and
// a static envelope schema for the API request shape.
// ---------------------------------------------------------------------------

function questionZod(
  question: Question,
  ctx: { required: boolean; allowed: Set<string> },
): z.ZodTypeAny {
  if (question.type === "text") {
    return z.string().superRefine((value, refine) => {
      const normalised = normaliseText(value);
      const message = checkText(normalised, question, ctx.required);
      if (message) refine.addIssue({ code: z.ZodIssueCode.custom, message });
      if (ctx.required && normalised === "") {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.required });
      }
    });
  }
  if (question.type === "single_choice") {
    return z.string().superRefine((value, refine) => {
      if (value === "") {
        if (ctx.required) {
          refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.required });
        }
        return;
      }
      if (!ctx.allowed.has(value)) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.unknownOption });
      }
    });
  }
  // multi_choice
  return z.array(z.string()).superRefine((values, refine) => {
    for (const value of values) {
      if (!ctx.allowed.has(value)) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.unknownOption });
        return;
      }
    }
    const message = checkMulti(values, question, ctx.required);
    if (message) refine.addIssue({ code: z.ZodIssueCode.custom, message });
    if (ctx.required && values.length === 0) {
      refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.required });
    }
  });
}

// A Zod object covering the VISIBLE questions of one step, built from the
// current answers (which determine visibility, required and allowed options).
// Changing a constraint in the schema changes this validation with no code
// change — the whole schema is read, nothing hard-coded.
export function buildStepZodSchema(
  schema: QuestionnaireSchema,
  step: Step,
  answers: Answers,
): z.ZodTypeAny {
  const visibility = visibleQuestions(schema, answers);
  const required = requiredQuestions(schema, answers, visibility);
  const allowed = allowedOptions(schema, answers);
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const question of step.questions) {
    if (!visibility[question.id]) continue;
    shape[question.id] = questionZod(question, {
      required: Boolean(required[question.id]),
      allowed: allowed[question.id] ?? new Set<string>(),
    });
  }
  return z.object(shape).partial();
}

// Static Zod schema for the stable API request envelope (shape only — the
// backend remains authoritative for the answer contents).
export const designEnvelopeSchema = z.object({
  title: z.string().max(120).optional(),
  questionnaire_version_id: z.string().uuid().optional(),
  answers: z.record(z.union([z.string(), z.array(z.string())])).optional(),
  inspiration_asset_ids: z.array(z.string().uuid()).max(3).optional(),
});
