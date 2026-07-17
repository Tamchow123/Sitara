// Questionnaire feature types. Server wire shapes are ALIASES of the
// generated OpenAPI components (never hand-maintained duplicates); only the
// client-only answer types are defined here.

import type { components } from "@/api/schema";

export type QuestionnaireSchema = components["schemas"]["QuestionnaireSchema"];
export type Step = components["schemas"]["StepSchema"];
export type Question = components["schemas"]["QuestionSchema"];
export type QuestionOption = components["schemas"]["QuestionOptionSchema"];
export type QuestionConstraints = components["schemas"]["QuestionConstraintsSchema"];
export type CompatibilityRule = components["schemas"]["CompatibilityRuleSchema"];
export type ActiveQuestionnaire = components["schemas"]["ActiveQuestionnaireResponse"];
export type PublicAsset = components["schemas"]["PublicInspirationAsset"];
export type InspirationCatalogue = components["schemas"]["InspirationCatalogueResponse"];
export type DesignDraft = components["schemas"]["DesignDetailResponse"];
export type SelectedInspiration = components["schemas"]["SelectedInspiration"];

// A single answer value: a string for single_choice/text, an ordered string
// array for multi_choice. Answers are keyed by stable question id — exactly
// the object the API persists.
export type AnswerValue = string | string[];
export type Answers = Record<string, AnswerValue>;

// Per-question validation messages, keyed by question id. The special key
// "__all__" carries a top-level (non-field) error.
export type FieldErrors = Record<string, string[]>;
export const TOP_LEVEL_ERROR_KEY = "__all__";
