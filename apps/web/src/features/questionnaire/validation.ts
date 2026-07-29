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

import { zodResolver } from "@hookform/resolvers/zod";
import type { Resolver } from "react-hook-form";
import { z } from "zod";

import {
  allowedOptions,
  buildSelected,
  declaredOptionValues,
  isAnswered,
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
  wrongColour: "This answer must be a single colour.",
  wrongColourList: "This answer must be a list of colours.",
  badHex: "A colour must be a six-digit hex value like #c67139.",
  duplicateColour: "The same colour was added more than once.",
  customNotAllowed: "This question does not accept your own colours.",
  unknownCustomColour: "That colour is no longer in your colours.",
};

// A six-digit hex colour, upper or lower case on input; always stored lower
// case. Mirrors the backend's colour_list/colour_choice rule exactly. Exported
// so answer cleanup applies the SAME definition of "this answer is a custom
// colour" rather than a second, drifting copy.
export const HEX_COLOUR_INPUT = /^#[0-9a-fA-F]{6}$/;

function normaliseColourList(value: unknown): { value?: string[]; message?: string } {
  if (!Array.isArray(value)) return { message: MSG.wrongColourList };
  const ordered: string[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    if (typeof item !== "string" || !HEX_COLOUR_INPUT.test(item)) return { message: MSG.badHex };
    const lowered = item.toLowerCase();
    if (seen.has(lowered)) return { message: MSG.duplicateColour };
    seen.add(lowered);
    ordered.push(lowered);
  }
  return { value: ordered };
}

// The design's own colours, resolved from the single `colour_list` question
// BEFORE the per-question pass — a colour_choice answer's legitimacy depends on
// it, and object iteration order gives no guarantee of reaching it first. A
// malformed palette yields an empty set, so a custom colour_choice referencing
// it fails too rather than being silently accepted.
export function customColourPalette(
  schema: QuestionnaireSchema,
  answers: Record<string, unknown>,
): Set<string> {
  for (const [questionId, question] of Object.entries(questionsById(schema))) {
    if (question.type !== "colour_list") continue;
    const result = normaliseColourList(answers[questionId]);
    return new Set(result.value ?? []);
  }
  return new Set();
}

export function normaliseText(value: string): string {
  // CRLF/CR → LF, trim OUTER whitespace, preserve internal whitespace.
  return value.replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

type Structural = { value?: AnswerValue; selected?: Set<string>; message?: string };

function structuralValue(
  question: Question,
  value: unknown,
  customPalette: Set<string>,
): Structural {
  const declared = new Set(declaredOptionValues(question));
  if (question.type === "colour_choice") {
    // A declared swatch or, when the question allows it, one of the design's
    // own colours. Never contributes a `selected` set: colours take no part in
    // rule evaluation, so a custom hex can never reach a condition.
    if (typeof value !== "string") return { message: MSG.wrongColour };
    if (declared.has(value)) return { value };
    if (!HEX_COLOUR_INPUT.test(value)) return { message: MSG.unknownOption };
    if (!question.constraints?.allow_custom) return { message: MSG.customNotAllowed };
    const lowered = value.toLowerCase();
    if (!customPalette.has(lowered)) return { message: MSG.unknownCustomColour };
    return { value: lowered };
  }
  if (question.type === "colour_list") {
    const result = normaliseColourList(value);
    return result.message ? { message: result.message } : { value: result.value };
  }
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
  const palette = customColourPalette(schema, answers);
  for (const [key, value] of Object.entries(answers)) {
    const question = index[key];
    if (!question) {
      errors[key] = [MSG.unknownQuestion];
      continue;
    }
    const result = structuralValue(question, value, palette);
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
    } else if (question.type === "colour_list") {
      // The palette's own bound. Colours are not options, so none of the
      // option-based checks above apply to it.
      const maxItems = question.constraints?.max_items;
      if (isNum(maxItems) && (value as string[]).length > maxItems) {
        errors[key] = [MSG.tooMany(maxItems)];
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

function isNum(value: unknown): value is number {
  return typeof value === "number" && !Number.isNaN(value);
}

// Per-question Zod, built from the machine constraints. Requiredness and
// minimum-constraint enforcement are SEPARATE concerns: an empty answer to an
// optional question is valid, but any SUPPLIED value obeys its minimum during
// complete step validation (a required question additionally must not be
// empty). Option membership and restrictions stay schema-derived.
function questionZod(
  question: Question,
  ctx: { required: boolean; allowed: Set<string>; customPalette: Set<string> },
): z.ZodTypeAny {
  const c = question.constraints ?? {};

  if (question.type === "colour_choice") {
    return z.string().superRefine((value, refine) => {
      if (!value) {
        if (ctx.required) {
          refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.required });
        }
        return;
      }
      if (ctx.allowed.has(value)) return;
      if (!HEX_COLOUR_INPUT.test(value)) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.unknownOption });
        return;
      }
      if (!c.allow_custom) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.customNotAllowed });
        return;
      }
      if (!ctx.customPalette.has(value.toLowerCase())) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.unknownCustomColour });
      }
    });
  }

  if (question.type === "colour_list") {
    return z.array(z.string()).superRefine((values, refine) => {
      const result = normaliseColourList(values ?? []);
      if (result.message) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: result.message });
        return;
      }
      const list = result.value ?? [];
      if (isNum(c.max_items) && list.length > c.max_items) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.tooMany(c.max_items) });
      }
      if (list.length === 0 && ctx.required) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.required });
      }
    });
  }

  if (question.type === "text") {
    return z.string().superRefine((raw, refine) => {
      const value = normaliseText(raw ?? "");
      if (value === "") {
        if (ctx.required) {
          refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.required });
        }
        return; // empty optional text is valid; no min/max on emptiness
      }
      if (isNum(c.max_length) && value.length > c.max_length) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.tooLong(c.max_length) });
      }
      if (isNum(c.min_length) && value.length < c.min_length) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.tooShort(c.min_length) });
      }
    });
  }

  if (question.type === "single_choice") {
    return z.string().superRefine((value, refine) => {
      if (!value) {
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
    const list = values ?? [];
    for (const value of list) {
      if (!ctx.allowed.has(value)) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.unknownOption });
        return;
      }
    }
    const exclusive = new Set(c.exclusive_values ?? []);
    if (exclusive.size > 0 && list.length > 1 && list.some((v) => exclusive.has(v))) {
      refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.exclusive });
    }
    if (isNum(c.max_items) && list.length > c.max_items) {
      refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.tooMany(c.max_items) });
    }
    if (list.length === 0) {
      if (ctx.required) {
        refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.required });
      }
      return; // empty optional multi-choice is valid; no min on emptiness
    }
    if (isNum(c.min_items) && list.length < c.min_items) {
      refine.addIssue({ code: z.ZodIssueCode.custom, message: MSG.tooFew(c.min_items) });
    }
  });
}

// A Zod object covering the VISIBLE questions of one step, built from the
// current answers (which determine visibility, required and allowed options).
// Required questions are structurally required; optional questions stay
// optional (no blanket `.partial()`). Changing a constraint in the schema
// changes this validation with no code change — the whole schema is read.
export function buildStepZodSchema(
  schema: QuestionnaireSchema,
  step: Step,
  answers: Answers,
): z.ZodTypeAny {
  const visibility = visibleQuestions(schema, answers);
  const required = requiredQuestions(schema, answers, visibility);
  const allowed = allowedOptions(schema, answers);
  const customPalette = customColourPalette(schema, answers);
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const question of step.questions) {
    if (!visibility[question.id]) continue;
    const isRequired = Boolean(required[question.id]);
    const base = questionZod(question, {
      required: isRequired,
      allowed: allowed[question.id] ?? new Set<string>(),
      customPalette,
    });
    // Optional questions may be absent entirely; required ones may not.
    shape[question.id] = isRequired ? base : base.optional();
  }
  return z.object(shape);
}

// Static Zod schema for the stable API request envelope — a SHAPE guard run
// before every outgoing create/update. Formats (uuid) are the backend's job;
// this only proves the client is sending a well-formed body.
export const designEnvelopeSchema = z.object({
  title: z.string().max(120).optional(),
  questionnaire_version_id: z.string().min(1).optional(),
  answers: z.record(z.union([z.string(), z.array(z.string())])).optional(),
  inspiration_asset_ids: z.array(z.string()).optional(),
});

// A React Hook Form resolver that validates the CURRENT visible step against
// its derived Zod schema. The wizard uses exactly this; tests use it to prove
// the RHF integration runs the derived schema. Context is read lazily so
// visibility/required/allowed always reflect the latest answers.
export function createStepResolver(
  getContext: () => { schema: QuestionnaireSchema | null; step: Step | null; answers: Answers },
): Resolver<Record<string, AnswerValue>> {
  return async (values, context, options) => {
    const { schema, step, answers } = getContext();
    if (!schema || !step) return { values, errors: {} };
    const zodSchema = buildStepZodSchema(schema, step, answers);
    return zodResolver(zodSchema)(values, context, options);
  };
}
