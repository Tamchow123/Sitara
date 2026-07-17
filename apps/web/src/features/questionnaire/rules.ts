// Pure evaluation of the allowlisted questionnaire compatibility rules —
// the frontend mirror of the backend's sitara.questionnaire.rules. Neither
// side hard-codes an individual fixture rule; both interpret the same
// machine-readable `when`/`then` vocabulary generically (ADR 0005, Phase 7).
//
// Semantics (identical to the backend):
// - A condition whose question has no current answer evaluates false.
// - A single-choice answer is one selected value; a multi-choice answer is a
//   set of selected values.
// - equals → selected values exactly equal the condition values.
// - in     → at least one selected value occurs in the condition values.
// - not_in → an answer exists AND none of its values occurs in the condition
//   values.
// - Questions targeted by any `show` rule are hidden by default; all others
//   are visible by default. A matching `show` reveals; a matching `hide`
//   hides; hide wins on conflict.
// - Base `required` applies only while visible; a matching `require` rule
//   makes a visible question required.
// - Matching `restrict_options` rules intersect their value sets; with no
//   matching restriction all declared options remain allowed.

import type {
  Answers,
  AnswerValue,
  CompatibilityRule,
  Question,
  QuestionnaireSchema,
} from "./types";

export function allQuestions(schema: QuestionnaireSchema): Question[] {
  return schema.steps.flatMap((step) => step.questions);
}

export function questionsById(
  schema: QuestionnaireSchema,
): Record<string, Question> {
  const index: Record<string, Question> = {};
  for (const question of allQuestions(schema)) index[question.id] = question;
  return index;
}

export function declaredOptionValues(question: Question): string[] {
  return (question.options ?? []).map((option) => option.value);
}

function selectedSet(value: AnswerValue | undefined): Set<string> | null {
  if (value === undefined || value === null) return null;
  if (typeof value === "string") return value === "" ? null : new Set([value]);
  if (Array.isArray(value)) return value.length > 0 ? new Set(value) : null;
  return null;
}

// The selected-values view rule evaluation needs, built from the answers.
export function buildSelected(
  schema: QuestionnaireSchema,
  answers: Answers,
): Record<string, Set<string>> {
  const selected: Record<string, Set<string>> = {};
  for (const question of allQuestions(schema)) {
    const set = selectedSet(answers[question.id]);
    if (set) selected[question.id] = set;
  }
  return selected;
}

function intersects(a: Set<string>, b: Set<string>): boolean {
  for (const value of a) if (b.has(value)) return true;
  return false;
}

function setsEqual(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false;
  for (const value of a) if (!b.has(value)) return false;
  return true;
}

function conditionMet(
  rule: CompatibilityRule,
  selected: Record<string, Set<string>>,
): boolean {
  const chosen = selected[rule.when.question_id];
  if (!chosen) return false; // no current answer → false
  const values = new Set(rule.when.values);
  switch (rule.when.operator) {
    case "equals":
      return setsEqual(chosen, values);
    case "in":
      return intersects(chosen, values);
    case "not_in":
      return !intersects(chosen, values);
    default:
      return false;
  }
}

function matchingTargets(
  schema: QuestionnaireSchema,
  selected: Record<string, Set<string>>,
  action: string,
): Set<string> {
  const targets = new Set<string>();
  for (const rule of schema.rules) {
    if (rule.then.action === action && conditionMet(rule, selected)) {
      targets.add(rule.then.question_id);
    }
  }
  return targets;
}

export function visibleQuestions(
  schema: QuestionnaireSchema,
  answers: Answers,
): Record<string, boolean> {
  const selected = buildSelected(schema, answers);
  const hiddenByDefault = new Set(
    schema.rules
      .filter((rule) => rule.then.action === "show")
      .map((rule) => rule.then.question_id),
  );
  const shown = matchingTargets(schema, selected, "show");
  const hidden = matchingTargets(schema, selected, "hide");
  const visibility: Record<string, boolean> = {};
  for (const question of allQuestions(schema)) {
    let visible = !hiddenByDefault.has(question.id);
    if (shown.has(question.id)) visible = true;
    if (hidden.has(question.id)) visible = false; // hide wins
    visibility[question.id] = visible;
  }
  return visibility;
}

export function requiredQuestions(
  schema: QuestionnaireSchema,
  answers: Answers,
  visibility: Record<string, boolean>,
): Record<string, boolean> {
  const selected = buildSelected(schema, answers);
  const requireTargets = matchingTargets(schema, selected, "require");
  const required: Record<string, boolean> = {};
  for (const question of allQuestions(schema)) {
    const base = Boolean(question.required);
    required[question.id] =
      (base || requireTargets.has(question.id)) && visibility[question.id];
  }
  return required;
}

export function allowedOptions(
  schema: QuestionnaireSchema,
  answers: Answers,
): Record<string, Set<string>> {
  const selected = buildSelected(schema, answers);
  const allowed: Record<string, Set<string>> = {};
  for (const question of allQuestions(schema)) {
    if (question.type === "text") continue;
    let declared = new Set(declaredOptionValues(question));
    for (const rule of schema.rules) {
      if (
        rule.then.action === "restrict_options" &&
        rule.then.question_id === question.id &&
        conditionMet(rule, selected)
      ) {
        const permitted = new Set(rule.then.values ?? []);
        declared = new Set([...declared].filter((value) => permitted.has(value)));
      }
    }
    allowed[question.id] = declared;
  }
  return allowed;
}
