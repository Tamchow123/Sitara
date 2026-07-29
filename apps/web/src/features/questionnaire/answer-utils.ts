// Pure helpers for manipulating answers against the schema: clearing answers
// that became hidden or disallowed, and resolving option labels for display.
// All derived from the schema — no question id is treated as a business rule.
//
// Deciding where the wizard resumes, and whether a screen's required questions
// are answered, belongs to `screens.ts` (Phase 16B): navigation is measured in
// SCREENS, not steps, and one definition of "answered" is enough.

import {
  allowedOptions,
  declaredOptionValues,
  questionsById,
  visibleQuestions,
} from "./rules";
import { HEX_COLOUR_INPUT } from "./validation";
import type { Answers, Question, QuestionnaireSchema } from "./types";

// The design's own colours, as they will SURVIVE this clean-up: the schema's
// single colour_list answer, but only while that question is still visible.
// Resolved before the per-answer pass because a colour_choice answer may be one
// of these hexes, and the backend rejects a hex the palette no longer carries —
// so a palette that is about to be dropped must take its references with it.
function survivingPalette(
  schema: QuestionnaireSchema,
  answers: Answers,
  visibility: Record<string, boolean>,
): Set<string> {
  for (const [questionId, question] of Object.entries(questionsById(schema))) {
    if (question.type !== "colour_list") continue;
    if (!visibility[questionId]) return new Set();
    const value = answers[questionId];
    if (!Array.isArray(value)) return new Set();
    const palette = new Set<string>();
    for (const entry of value) {
      // Total over arbitrary stored answers: a malformed palette fails CLOSED
      // with an empty set — the same thing the backend's own _custom_palette
      // does — and is reported on the palette's own question by validation.
      // This runs on every answer change, so it must never throw.
      if (typeof entry !== "string") return new Set();
      palette.add(entry.toLowerCase());
    }
    return palette;
  }
  return new Set();
}

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
  const palette = survivingPalette(schema, answers, visibility);
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
    if (question.type === "colour_list") {
      // The design's own palette: user-supplied hex values, never schema
      // options, so the allow-set does not apply. Kept verbatim (the backend
      // normalises and bounds it) rather than dropped as a non-string answer.
      if (Array.isArray(value) && value.length > 0) cleaned[key] = value;
      continue;
    }
    if (question.type === "colour_choice") {
      // Either a declared swatch or one of the palette's own hex colours —
      // which is legitimately absent from the allow-set, so filtering by it
      // here would silently erase every custom colour choice. Colours never
      // participate in rules, so nothing can restrict them anyway.
      if (typeof value !== "string" || value === "") continue;
      // A custom colour is only valid while the palette still carries it: keep
      // the two in step here rather than letting the backend reject the save.
      if (HEX_COLOUR_INPUT.test(value) && !palette.has(value.toLowerCase())) continue;
      cleaned[key] = value;
      continue;
    }
    // text
    if (typeof value === "string" && value !== "") cleaned[key] = value;
  }
  return cleaned;
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
