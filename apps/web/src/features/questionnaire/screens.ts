// One question per screen (Phase 16B, T7).
//
// The schema's steps are the questionnaire's PERSISTENCE and grouping unit;
// the handoff asks a bride one thing at a time. This module is the only place
// that reconciles the two: a step becomes a progress CATEGORY, and each of its
// visible questions becomes its own SCREEN inside that category.
//
// The single exception is colour. A step's colour questions (the per-role
// `colour_choice` questions and the design's own `colour_list` palette) are
// answered against one shared swatch grid, and splitting them would ask the
// same question three times with the same controls; they collapse into one
// screen titled by the step. That rule is expressed by question TYPE, never by
// question id, so a schema that adds a fourth colour role needs no change here.
//
// Screens are derived from the schema AND the current answers, so hiding a
// question (choosing a saree hides the dupatta question) removes its screen
// immediately. Nothing here is persisted: the wizard holds a screen index, and
// answers remain the only source of truth.

import { isAnswered, visibleQuestions } from "./rules";
import type { Answers, Question, QuestionnaireSchema } from "./types";

// The inspiration screen has no schema step of its own. Double-underscored so
// it can never collide with a schema step id.
export const INSPIRATION_CATEGORY_ID = "__inspiration__";
export const INSPIRATION_SCREEN_ID = "__inspiration__";

const COLOUR_TYPES = new Set(["colour_choice", "colour_list"]);

export type ScreenCategory = {
  // Stable machine id: a schema step id, or INSPIRATION_CATEGORY_ID.
  id: string;
  label: string;
  // Index of this category's FIRST screen — where its progress pill jumps to.
  firstScreen: number;
};

export type Screen = {
  // Stable machine id: the id of the first question shown, or the synthetic
  // inspiration id. Used for deep links and as a React key, never persisted.
  id: string;
  categoryId: string;
  // Index into the categories array (NOT the screen index).
  categoryIndex: number;
  // The screen's heading. A single-question screen borrows the question's own
  // label so the h1 IS the question; a grouped colour screen uses the step
  // title, because no one question speaks for the group.
  title: string;
  // True when `title` IS the sole question's label, so the field must not
  // print that label a SECOND time under the heading. The control still needs
  // its accessible name, so the label is hidden visually, never removed.
  titleIsQuestionLabel: boolean;
  description?: string;
  // Empty for the inspiration screen, which renders its own picker.
  questions: Question[];
  // Position of this screen within its category, and the category's total —
  // the handoff's "Question x of y" kicker.
  positionInCategory: number;
  screensInCategory: number;
};

export type ScreenPlan = {
  screens: Screen[];
  categories: ScreenCategory[];
};

// Build the whole plan for the CURRENT answers. A step whose questions are all
// hidden contributes no screens, and therefore no category: an unreachable pill
// would make "Step n of m" a lie.
export function buildScreenPlan(
  schema: QuestionnaireSchema,
  answers: Answers,
): ScreenPlan {
  const visibility = visibleQuestions(schema, answers);
  const screens: Screen[] = [];
  const categories: ScreenCategory[] = [];

  for (const step of schema.steps) {
    const visible = step.questions.filter((question) => visibility[question.id]);
    if (visible.length === 0) continue;

    const colours = visible.filter((question) => COLOUR_TYPES.has(question.type));
    const others = visible.filter((question) => !COLOUR_TYPES.has(question.type));
    const groups: Question[][] = others.map((question) => [question]);
    if (colours.length > 0) {
      // Colours lead their step: they are the step's subject wherever they
      // appear, and the handoff shows them as the whole screen.
      groups.unshift(colours);
    }

    const categoryIndex = categories.length;
    const firstScreen = screens.length;
    groups.forEach((questions, position) => {
      const grouped = questions.length > 1 || COLOUR_TYPES.has(questions[0].type);
      screens.push({
        id: questions[0].id,
        categoryId: step.id,
        categoryIndex,
        title: grouped ? step.title : questions[0].label,
        titleIsQuestionLabel: !grouped,
        description: grouped ? (step.description ?? undefined) : undefined,
        questions,
        positionInCategory: position + 1,
        screensInCategory: groups.length,
      });
    });
    categories.push({ id: step.id, label: step.title, firstScreen });
  }

  categories.push({
    id: INSPIRATION_CATEGORY_ID,
    label: "Inspiration",
    firstScreen: screens.length,
  });
  screens.push({
    id: INSPIRATION_SCREEN_ID,
    categoryId: INSPIRATION_CATEGORY_ID,
    categoryIndex: categories.length - 1,
    title: "Which looks inspire you?",
    titleIsQuestionLabel: false,
    questions: [],
    positionInCategory: 1,
    screensInCategory: 1,
  });

  return { screens, categories };
}

export function isInspirationScreen(screen: Screen | undefined): boolean {
  return screen?.id === INSPIRATION_SCREEN_ID;
}

// Whether every REQUIRED question on this screen is answered. Drives the
// handoff's disabled Continue and its hidden Skip; the Zod resolver still
// revalidates on submit, and Django remains authoritative.
export function isScreenAnswered(
  screen: Screen,
  answers: Answers,
  required: Record<string, boolean>,
): boolean {
  return screen.questions.every(
    (question) => !required[question.id] || isAnswered(question, answers[question.id]),
  );
}

export function hasRequiredQuestion(
  screen: Screen,
  required: Record<string, boolean>,
): boolean {
  return screen.questions.some((question) => required[question.id]);
}

// The first screen with an unanswered required question, or -1 when there is
// none outstanding.
//
// Deliberately NOT resumeScreenIndex below. That one answers "where should a
// returning user land", so it falls back to the inspiration screen and can
// never say "nothing is outstanding" — index 0 means both "the very first
// question is unanswered" and, for a plan of one screen, "you are done". A
// navigation ceiling has to tell those apart, so this returns -1 for a
// complete draft and never treats the inspiration screen as a blocker (it has
// no required questions of its own).
export function firstUnansweredScreenIndex(
  plan: ScreenPlan,
  answers: Answers,
  required: Record<string, boolean>,
): number {
  return plan.screens.findIndex(
    (screen) => !isInspirationScreen(screen) && !isScreenAnswered(screen, answers, required),
  );
}

// The screen a resumed draft should open on: the first whose required questions
// are not all answered, or the inspiration screen when the questionnaire is
// complete. Screen-based rather than step-based, so resuming lands on the exact
// question left unanswered instead of the top of its category.
export function resumeScreenIndex(
  plan: ScreenPlan,
  answers: Answers,
  required: Record<string, boolean>,
): number {
  for (let index = 0; index < plan.screens.length; index += 1) {
    const screen = plan.screens[index];
    if (isInspirationScreen(screen)) return index;
    if (!isScreenAnswered(screen, answers, required)) return index;
  }
  return Math.max(plan.screens.length - 1, 0);
}

// Where a review-screen "Edit" link should land. Unknown ids (a question that
// has since been hidden) resolve to nothing rather than to screen 0, so a stale
// link never silently sends the user somewhere unrelated.
export function screenIndexForQuestion(plan: ScreenPlan, questionId: string): number | null {
  const index = plan.screens.findIndex((screen) =>
    screen.questions.some((question) => question.id === questionId),
  );
  return index === -1 ? null : index;
}
