// Pure helpers for manipulating answers against the schema: clearing answers
// that became hidden or disallowed, resolving option labels for display, and
// deciding where to resume. All derived from the schema — no question id is
// treated as a business rule.

import {
  allowedOptions,
  declaredOptionValues,
  questionsById,
  requiredQuestions,
  visibleQuestions,
} from "./rules";
import type { Answers, Question, QuestionnaireSchema, Step } from "./types";

// Remove answers that are no longer applicable: hidden questions, and choice
// values that are no longer allowed by the active restrictions. Returns a new
// object; the input is never mutated. Run whenever a controlling answer
// changes so stale silhouette/draping answers disappear.
export function clearStaleAnswers(
  schema: QuestionnaireSchema,
  answers: Answers,
): Answers {
  const index = questionsById(schema);
  const visibility = visibleQuestions(schema, answers);
  const allowed = allowedOptions(schema, answers);
  const cleaned: Answers = {};
  for (const [key, value] of Object.entries(answers)) {
    const question = index[key];
    if (!question) continue; // unknown question id
    if (!visibility[key]) continue; // hidden → drop
    if (question.type === "single_choice") {
      if (typeof value === "string" && (allowed[key]?.has(value) ?? false)) {
        cleaned[key] = value;
      }
      continue;
    }
    if (question.type === "multi_choice") {
      const allow = allowed[key] ?? new Set<string>();
      const kept = Array.isArray(value) ? value.filter((v) => allow.has(v)) : [];
      if (kept.length > 0) cleaned[key] = kept;
      continue;
    }
    // text
    if (typeof value === "string" && value !== "") cleaned[key] = value;
  }
  return cleaned;
}

export function visibleStepQuestions(
  schema: QuestionnaireSchema,
  step: Step,
  answers: Answers,
): Question[] {
  const visibility = visibleQuestions(schema, answers);
  return step.questions.filter((question) => visibility[question.id]);
}

// A step is complete when every visible required question in it is answered.
export function isStepComplete(
  schema: QuestionnaireSchema,
  step: Step,
  answers: Answers,
): boolean {
  const visibility = visibleQuestions(schema, answers);
  const required = requiredQuestions(schema, answers, visibility);
  for (const question of step.questions) {
    if (!required[question.id]) continue;
    const value = answers[question.id];
    const answered =
      question.type === "multi_choice"
        ? Array.isArray(value) && value.length > 0
        : typeof value === "string" && value !== "";
    if (!answered) return false;
  }
  return true;
}

// The first step (0-based) whose required visible questions are not all
// answered, so a resumed draft lands where the user left off. Returns
// steps.length when every questionnaire step is complete (i.e. proceed to the
// inspiration step).
export function resumeStepIndex(
  schema: QuestionnaireSchema,
  answers: Answers,
): number {
  for (let index = 0; index < schema.steps.length; index += 1) {
    if (!isStepComplete(schema, schema.steps[index], answers)) return index;
  }
  return schema.steps.length;
}

// The human label for a stored option value, resolved from the schema — never
// hard-coded. Falls back to the raw value if the option is unknown.
export function optionLabel(question: Question, value: string): string {
  const option = (question.options ?? []).find((candidate) => candidate.value === value);
  return option?.label ?? value;
}

export function answerLabels(question: Question, value: Answers[string]): string[] {
  if (question.type === "text") {
    return typeof value === "string" && value !== "" ? [value] : [];
  }
  if (typeof value === "string") {
    return value === "" ? [] : [optionLabel(question, value)];
  }
  if (Array.isArray(value)) {
    return value.map((entry) => optionLabel(question, entry));
  }
  return [];
}

export function declaredOptions(question: Question): string[] {
  return declaredOptionValues(question);
}
