"use client";

// The schema-driven questionnaire wizard. It fetches the active questionnaire
// through the generated GET client, renders ONE question per screen from the
// schema, applies show/hide/require/restrict rules immediately, clears stale
// answers, validates the current screen through React Hook Form + a derived Zod
// resolver, and persists progress to the private Design through a single-flight
// save coordinator (never to browser storage). The Design is created on the
// first successful save (not on page view); resume reconstructs the wizard from
// the persisted answers and the design's linked questionnaire. Backend
// validation stays authoritative.
//
// Screens vs categories (Phase 16B): the schema's steps are the progress
// CATEGORIES shown in the nav, and each visible question inside a step is its
// own SCREEN. `screens.ts` owns that mapping — including the one grouping rule,
// where a step's colour questions share a screen. The wizard holds a screen
// index; answers remain the only persisted state.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { useRouter } from "next/navigation";

import { fetchActiveQuestionnaire, fetchCatalogue, fetchDesign } from "./api";
import type { InspirationUpload } from "@/lib/api";

import { InspirationPicker } from "./InspirationPicker";
import { ProgressNav } from "./ProgressNav";
import { QuestionField } from "./QuestionField";
import { allowedOptions, questionsById, requiredQuestions, visibleQuestions } from "./rules";
import { clearStaleAnswers } from "./answer-utils";
import {
  buildScreenPlan,
  firstUnansweredScreenIndex,
  hasRequiredQuestion,
  isInspirationScreen,
  isScreenAnswered,
  resumeScreenIndex,
  screenIndexForQuestion,
} from "./screens";
import { createStepResolver } from "./validation";
import { useDraftSaver } from "./use-draft-saver";
import { useLatest } from "./use-latest";
import { resolveDesignLifecycleTarget } from "@/lib/design-lifecycle";
import type { ProgressCategory } from "./ProgressNav";
import type { Screen } from "./screens";
import type { Answers, AnswerValue, PublicAsset, QuestionnaireSchema, Step } from "./types";

const MAX_INSPIRATIONS = 3;

// The review screen's per-row Edit links arrive as ?q=<question id>. Read once,
// from the URL rather than a Next hook, so the wizard needs no Suspense
// boundary and no extra router mocking in tests.
const EDIT_QUESTION_PARAM = "q";

type LoadState = "loading" | "ready" | "unavailable" | "notfound" | "redirecting";
type CatalogueState = {
  status: "idle" | "loading" | "ready" | "unavailable";
  assets: PublicAsset[];
};

type Props = { initialDesignId?: string };
type StepValues = Record<string, AnswerValue>;

// The resolver validates a STEP; a screen is a step-shaped slice of one. Built
// here rather than in screens.ts so the wire type stays where the other wire
// types are used.
function screenAsStep(screen: Screen): Step {
  return { id: screen.categoryId, title: screen.title, questions: screen.questions };
}

function requestedQuestionId(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(EDIT_QUESTION_PARAM);
}

export function QuestionnaireWizard({ initialDesignId }: Props) {
  const router = useRouter();

  const [load, setLoad] = useState<LoadState>("loading");
  const [reloadCounter, setReloadCounter] = useState(0);
  const [schema, setSchema] = useState<QuestionnaireSchema | null>(null);
  const [versionId, setVersionId] = useState<string>("");
  const [answers, setAnswers] = useState<Answers>({});
  const [selection, setSelection] = useState<string[]>([]);
  // The user's own uploaded references. Held here rather than inside the picker
  // so a resumed design restores them from the server, and so both kinds of
  // reference are counted against the one shared cap in a single place.
  const [uploads, setUploads] = useState<InspirationUpload[]>([]);
  const [catalogue, setCatalogue] = useState<CatalogueState>({ status: "idle", assets: [] });
  const [catalogueAttempt, setCatalogueAttempt] = useState(0);
  const [screenIndex, setScreenIndex] = useState(0);
  // The furthest screen reached so far. Progress-nav jumps are limited to it,
  // so a pill can never carry the user past a screen whose required answers
  // have not been validated — forward movement still goes through Continue.
  const [maxReached, setMaxReached] = useState(0);
  const [errorTick, setErrorTick] = useState(0);

  const answersRef = useRef<Answers>({});
  const errorSummaryRef = useRef<HTMLDivElement>(null);

  const setAnswersSynced = useCallback((next: Answers) => {
    answersRef.current = next;
    setAnswers(next);
  }, []);

  // Synchronised DURING render (not in a post-render effect) so the RHF screen
  // resolver always sees the CURRENT schema and screen the instant a Continue
  // click fires — never null/stale, which would bypass or misdirect
  // validation. answersRef stays synchronous via setAnswersSynced above.
  const schemaRef = useLatest(schema);
  const screenIndexRef = useLatest(screenIndex);

  const saver = useDraftSaver({
    versionId,
    onCreated: (design) => router.replace(`/design/${design.id}`),
  });
  const { flush, retry, save, saveText, adopt } = saver;

  const plan = useMemo(
    () => (schema ? buildScreenPlan(schema, answers) : null),
    [schema, answers],
  );
  const planRef = useLatest(plan);

  // Visibility and requiredness gate navigation here AND drive the render pass
  // further down. Computed once so the two can never disagree about whether a
  // question still counts.
  const visibility = useMemo(
    () => (schema ? visibleQuestions(schema, answers) : {}),
    [schema, answers],
  );
  const required = useMemo(
    () => (schema ? requiredQuestions(schema, answers, visibility) : {}),
    [schema, answers, visibility],
  );

  // How far the user may move. `maxReached` records how far they HAVE got, but
  // it is monotonic and says nothing about whether the screens behind them are
  // still answered. An answer can disappear after the fact: clearStaleAnswers
  // drops one whenever a controlling choice changes (switching the garment from
  // lehenga to saree discards the lehenga silhouette), and a `show` rule can
  // reveal a required question the user has already walked past. Without this
  // ceiling a progress pill carries them over the gap to the review screen,
  // where the first they hear of it is a server 400 listing questions they were
  // never stopped on.
  const frontier = useMemo(() => {
    if (!plan) return 0;
    const gap = firstUnansweredScreenIndex(plan, answers, required);
    return gap === -1 ? maxReached : Math.min(maxReached, gap);
  }, [plan, answers, required, maxReached]);
  const frontierRef = useLatest(frontier);
  const requiredRef = useLatest(required);

  // React Hook Form drives the CURRENT visible screen; the cross-screen Answers
  // object remains the source of truth (RHF mirrors it through `values`). The
  // resolver reads the latest screen/answers lazily so visibility, requiredness
  // and option restrictions always reflect the newest answers.
  const stepResolver = useMemo(
    () =>
      createStepResolver(() => {
        const currentSchema = schemaRef.current;
        const currentPlan = planRef.current;
        const screen = currentPlan?.screens[screenIndexRef.current];
        return {
          schema: currentSchema,
          step: screen && !isInspirationScreen(screen) ? screenAsStep(screen) : null,
          answers: answersRef.current,
        };
      }),
    // These are stable useLatest refs (identity never changes); listed to
    // satisfy exhaustive-deps.
    [schemaRef, planRef, screenIndexRef],
  );
  const form = useForm<StepValues>({
    values: answers as StepValues,
    resolver: stepResolver,
    mode: "onSubmit",
  });

  const currentScreen = plan?.screens[screenIndex] ?? null;
  const onInspirationScreen = currentScreen !== null && isInspirationScreen(currentScreen);

  // The design's own colour palette lives in the schema's single `colour_list`
  // question. Found BY TYPE, never by id: it is shared by every colour question
  // and edited through their pickers, so the wizard (which owns cross-question
  // answers) is the only place that can supply it.
  const colourListQuestion = useMemo(() => {
    for (const step of schema?.steps ?? []) {
      for (const question of step.questions) {
        if (question.type === "colour_list") return question;
      }
    }
    return null;
  }, [schema]);

  // -- Load / resume --------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoad("loading");
    async function loadWizard() {
      try {
        if (initialDesignId) {
          const design = await fetchDesign(initialDesignId);
          if (cancelled) return;
          const target = resolveDesignLifecycleTarget(design);
          if (target.kind === "progress" || target.kind === "result") {
            setLoad("redirecting");
            router.replace(target.href);
            return;
          }
          if (target.kind === "unavailable") {
            setLoad("unavailable");
            return;
          }
          let loadedSchema: QuestionnaireSchema;
          let loadedVersionId: string;
          if (design.questionnaire) {
            loadedSchema = design.questionnaire.schema;
            loadedVersionId = design.questionnaire.id;
          } else {
            const active = await fetchActiveQuestionnaire();
            loadedSchema = active.schema;
            loadedVersionId = active.id;
          }
          if (cancelled) return;
          const loadedAnswers = (design.answers ?? {}) as Answers;
          setSchema(loadedSchema);
          setVersionId(loadedVersionId);
          adopt(design.id);
          setAnswersSynced(loadedAnswers);
          setSelection(design.selected_inspirations.map((entry) => entry.id));
          setUploads(design.inspiration_uploads ?? []);
          const loadedPlan = buildScreenPlan(loadedSchema, loadedAnswers);
          const visibility = visibleQuestions(loadedSchema, loadedAnswers);
          const required = requiredQuestions(loadedSchema, loadedAnswers, visibility);
          const resumed = resumeScreenIndex(loadedPlan, loadedAnswers, required);
          // A resumed design has already answered its way to this screen, so
          // everything up to it is navigable again.
          setMaxReached(resumed);
          // A review "Edit" deep link wins over the resume position, but only
          // within what has already been reached — it can never skip ahead.
          const requested = requestedQuestionId();
          const requestedScreen = requested
            ? screenIndexForQuestion(loadedPlan, requested)
            : null;
          setScreenIndex(
            requestedScreen === null ? resumed : Math.min(requestedScreen, resumed),
          );
          setLoad("ready");
        } else {
          const active = await fetchActiveQuestionnaire();
          if (cancelled) return;
          setSchema(active.schema);
          setVersionId(active.id);
          setLoad("ready");
        }
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : "";
        setLoad(initialDesignId && message === "not_found" ? "notfound" : "unavailable");
      }
    }
    void loadWizard();
    return () => {
      cancelled = true;
    };
    // router is intentionally omitted: Next.js guarantees a stable
    // reference, and including it would re-run this effect (and refetch)
    // whenever a caller's router mock is not memoised.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialDesignId, reloadCounter, setAnswersSynced, adopt]);

  // -- Catalogue (loaded lazily; empty is valid, failure is distinct) --------
  // Gated on catalogueAttempt (an explicit trigger), never on catalogue.status
  // itself: setting catalogue.status inside this effect always schedules a
  // re-render in which the effect's own dependency array has changed, so
  // React tears the effect down (cancelled = true) and re-runs it — the
  // re-run's guard then sees "loading" (not "idle") and bails out without
  // starting a replacement fetch, so when the original fetch resolves (which
  // it always does after that re-render for any real, non-instant network
  // call) its result is discarded by the stale cancelled flag, leaving the
  // catalogue stuck loading forever.
  useEffect(() => {
    if (load !== "ready" || !onInspirationScreen) return;
    let cancelled = false;
    setCatalogue({ status: "loading", assets: [] });
    fetchCatalogue()
      .then((response) => {
        if (!cancelled) setCatalogue({ status: "ready", assets: response.assets });
      })
      .catch(() => {
        // A network/timeout/malformed/5xx failure is UNAVAILABLE — never a
        // silent empty catalogue.
        if (!cancelled) setCatalogue({ status: "unavailable", assets: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [load, onInspirationScreen, catalogueAttempt]);

  useEffect(() => {
    if (errorTick > 0) errorSummaryRef.current?.focus();
  }, [errorTick]);

  // Hiding a question removes its screen, so in principle an index can outrun
  // the plan. A BACKSTOP, not a reachable path: answers can only change on a
  // question screen, and every screen behind the one being edited survives, so
  // nothing today leaves the index past the end. It is kept because the cost of
  // being wrong is a blank screen (`currentScreen` would be undefined) rather
  // than an error, which is the kind of failure nobody reports. Clamping keeps
  // the user on the nearest surviving screen instead of throwing them back to
  // the start.
  useEffect(() => {
    if (!plan) return;
    const last = Math.max(plan.screens.length - 1, 0);
    if (screenIndex > last) setScreenIndex(last);
    if (maxReached > last) setMaxReached(last);
  }, [plan, screenIndex, maxReached]);

  // -- Answer / selection changes -------------------------------------------
  const onAnswerChange = useCallback(
    (questionId: string, value: AnswerValue) => {
      if (!schema) return;
      const next = clearStaleAnswers(schema, { ...answersRef.current, [questionId]: value });
      setAnswersSynced(next);
      const question = questionsById(schema)[questionId];
      if (question?.type === "text") saveText({ answers: next });
      else save({ answers: next });
    },
    [schema, save, saveText, setAnswersSynced],
  );

  const onAnswerBlur = useCallback(
    (questionId: string) => {
      const question = schema ? questionsById(schema)[questionId] : undefined;
      if (question?.type === "text") void flush(); // flush the debounce immediately
    },
    [schema, flush],
  );

  const onSelectionChange = useCallback(
    (ids: string[]) => {
      setSelection(ids);
      save({ inspiration_asset_ids: ids });
    },
    [save],
  );

  // -- Navigation (always flushes pending saves first) -----------------------
  // Every navigation awaits a save flush before it moves, so two of them
  // overlapping would let promise-resolution order — not click order — decide
  // where the user lands. One at a time: the ref rejects a second start even
  // within a single tick, and `navigating` disables every navigation control
  // (Back, Skip, Continue and all progress-nav pills) while one is in flight.
  const navigationInFlight = useRef(false);
  const [navigating, setNavigating] = useState(false);

  const runNavigation = useCallback(async (move: () => Promise<unknown>) => {
    if (navigationInFlight.current) return;
    navigationInFlight.current = true;
    setNavigating(true);
    try {
      await move();
    } finally {
      navigationInFlight.current = false;
      setNavigating(false);
    }
  }, []);

  const goBack = useCallback(
    () =>
      runNavigation(async () => {
        form.clearErrors();
        // Flush pending work before leaving; even if it fails the local values
        // stay visible and Retry is offered, so Back still returns to the
        // prior screen.
        await flush();
        setScreenIndex((index) => Math.max(0, index - 1));
      }),
    [flush, form, runNavigation],
  );

  // Progress-nav jump. Clamped to the frontier here as well as being offered
  // only for unlocked categories, so the guarantee does not depend on the
  // navigation component alone.
  const goToScreen = useCallback(
    (target: number) =>
      runNavigation(async () => {
        form.clearErrors();
        await flush();
        setScreenIndex(Math.min(Math.max(target, 0), frontierRef.current));
      }),
    [flush, form, frontierRef, runNavigation],
  );

  const advance = useCallback(async () => {
    const saved = await flush();
    if (!saved) {
      // Required save failed — do not advance; error + Retry are shown.
      setErrorTick((tick) => tick + 1);
      return;
    }
    const currentPlan = planRef.current;
    // Enforced here and not only by the disabled Continue button: the button
    // reflects the CURRENT screen, while an answer can go missing on a screen
    // behind the user (see the frontier above). Forward movement stops at the
    // outstanding question wherever it is.
    const gap = currentPlan
      ? firstUnansweredScreenIndex(currentPlan, answersRef.current, requiredRef.current)
      : -1;
    const screen = currentPlan?.screens[screenIndexRef.current];
    if (screen && isInspirationScreen(screen)) {
      // Never hand the user to the review screen with a required question
      // outstanding — that is exactly the path that ends in "you missed some
      // questions" once it is too late to be useful. Take them to it instead.
      if (gap !== -1) {
        setScreenIndex(gap);
        return;
      }
      if (saver.designId) router.push(`/design/${saver.designId}/review`);
      return;
    }
    const nextIndex = screenIndexRef.current + 1;
    const target = gap !== -1 && gap < nextIndex ? gap : nextIndex;
    setScreenIndex(target);
    setMaxReached((reached) => Math.max(reached, target));
  }, [flush, planRef, screenIndexRef, answersRef, requiredRef, saver.designId, router]);

  const goForward = useMemo(
    () => form.handleSubmit(advance, () => setErrorTick((tick) => tick + 1)),
    [form, advance],
  );

  // Skip leaves the question unanswered and moves on — the explicit,
  // non-destructive counterpart of "No preference". It bypasses validation
  // (there is nothing required to validate) but NOT the save flush, so a
  // skipped screen still commits whatever the user did touch.
  const goSkip = useCallback(
    () =>
      runNavigation(async () => {
        form.clearErrors();
        await advance();
      }),
    [advance, form, runNavigation],
  );

  // -- Render ---------------------------------------------------------------
  if (load === "loading" || load === "redirecting") {
    return (
      <p role="status" aria-live="polite">
        Loading the questionnaire…
      </p>
    );
  }
  if (load === "notfound") {
    return (
      <div role="alert">
        <h1>Design not found</h1>
        <p>This design is not available. It may belong to a different session.</p>
      </div>
    );
  }
  if (load === "unavailable" || schema === null || plan === null) {
    return (
      <div role="alert" className="wizard-unavailable">
        <p>The questionnaire is temporarily unavailable.</p>
        <button type="button" onClick={() => setReloadCounter((count) => count + 1)}>
          Try again
        </button>
      </div>
    );
  }

  const allowed = allowedOptions(schema, answers);
  const index = questionsById(schema);

  const activeCategoryIndex = plan.screens[screenIndex]?.categoryIndex ?? 0;
  // The handoff locks every category until the required openers (the ceremony
  // and the garment) are answered. The frontier IS that rule: it only advances
  // through Continue, and Continue is unavailable while the screen in front of
  // the user has an unanswered required question — so nothing beyond the
  // opening category can have been reached until they are answered.
  //
  // Read the frontier rather than maxReached so the nav tells the truth after
  // an answer disappears: a category whose question was cleared by a later
  // change goes back to incomplete and everything past it re-locks, instead of
  // staying ticked and offering a jump straight over the gap.
  const categories: ProgressCategory[] = plan.categories.map((category, position) => {
    // Complete once the user has moved past the category's LAST screen, which
    // is the next category's first (or the end of the plan).
    const endsBefore = plan.categories[position + 1]?.firstScreen ?? plan.screens.length;
    return {
      id: category.id,
      label: category.label,
      step: category.firstScreen,
      complete: frontier >= endsBefore,
      locked: category.firstScreen > frontier,
    };
  });

  const paletteValue = colourListQuestion ? answers[colourListQuestion.id] : undefined;
  const customColours = Array.isArray(paletteValue) ? paletteValue : [];
  const addCustomColour = colourListQuestion
    ? (hex: string) => onAnswerChange(colourListQuestion.id, [...customColours, hex])
    : undefined;

  const rhfErrors = form.formState.errors;
  const serverErrors = saver.fieldErrors;
  const errorKeys = Array.from(
    new Set([...Object.keys(rhfErrors), ...Object.keys(serverErrors)]),
  );
  const errorMessageFor = (key: string): string | undefined => {
    const rhf = rhfErrors[key]?.message;
    if (typeof rhf === "string" && rhf) return rhf;
    const server = serverErrors[key];
    return server && server.length > 0 ? server.join(" ") : undefined;
  };

  const saveStatus = (
    <p className="save-status" role="status" aria-live="polite">
      {saver.saveState === "saving" && "Saving…"}
      {saver.saveState === "saved" && "Saved"}
      {saver.saveState === "error" && (
        <span className="save-error">
          {saver.saveError ?? "Could not save."}{" "}
          <button type="button" onClick={() => void retry()}>
            Retry
          </button>
        </span>
      )}
    </p>
  );

  const screen = currentScreen;
  const screenRequired = screen ? hasRequiredQuestion(screen, required) : false;
  const screenAnswered = screen ? isScreenAnswered(screen, answers, required) : true;
  const activeCategory = plan.categories[activeCategoryIndex];
  // The handoff's kicker reads "Step n of m — Category · Question x of y". The
  // first half is already the progress nav's own label, directly above this
  // line, so repeating it would put the same sentence in the accessibility
  // tree twice; only the part the nav cannot say is rendered here, and only
  // when a category actually holds more than one question.
  const kicker =
    screen && !onInspirationScreen && activeCategory && screen.screensInCategory > 1
      ? `Question ${screen.positionInCategory} of ${screen.screensInCategory}`
      : null;

  return (
    <main className="wizard">
      <ProgressNav
        categories={categories}
        activeIndex={activeCategoryIndex}
        busy={navigating}
        onNavigate={(target) => void goToScreen(target)}
      />

      {errorKeys.length > 0 && (
        <div
          className="error-summary"
          role="alert"
          tabIndex={-1}
          ref={errorSummaryRef}
          aria-label="There is a problem"
        >
          <h2>Please review your answers</h2>
          <ul>
            {errorKeys.map((key) => (
              <li key={key}>
                {index[key] ? index[key].label : key}: {errorMessageFor(key)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {onInspirationScreen ? (
        <section aria-labelledby="inspiration-heading">
          <h1 id="inspiration-heading">Inspiration images</h1>
          {catalogue.status === "loading" || catalogue.status === "idle" ? (
            <p role="status" aria-live="polite">
              Loading inspiration images…
            </p>
          ) : catalogue.status === "unavailable" ? (
            <div role="alert" className="wizard-unavailable">
              <p>Inspiration images are temporarily unavailable.</p>
              <button
                type="button"
                onClick={() => setCatalogueAttempt((attempt) => attempt + 1)}
              >
                Try again
              </button>
            </div>
          ) : (
            <InspirationPicker
              assets={catalogue.assets}
              selection={selection}
              max={MAX_INSPIRATIONS}
              onChange={onSelectionChange}
              designId={saver.designId ?? undefined}
              uploads={uploads}
              onUploadsChange={setUploads}
            />
          )}
        </section>
      ) : (
        screen && (
          <section aria-labelledby="screen-heading">
            {kicker ? <p className="wizard-kicker">{kicker}</p> : null}
            <h1 id="screen-heading">{screen.title}</h1>
            {screen.description ? (
              <p className="step-description">{screen.description}</p>
            ) : null}
            <form onSubmit={(event) => event.preventDefault()}>
              {screen.questions.map((question) => (
                <Controller
                  key={question.id}
                  name={question.id}
                  control={form.control}
                  render={({ field }) => (
                    <QuestionField
                      question={question}
                      value={field.value}
                      error={errorMessageFor(question.id)}
                      allowed={allowed[question.id] ?? new Set<string>()}
                      labelHidden={screen.titleIsQuestionLabel}
                      customColours={customColours}
                      customColourMax={colourListQuestion?.constraints?.max_items ?? undefined}
                      onAddCustomColour={addCustomColour}
                      onChange={(value) => {
                        field.onChange(value);
                        onAnswerChange(question.id, value);
                      }}
                      onBlur={() => {
                        field.onBlur();
                        onAnswerBlur(question.id);
                      }}
                    />
                  )}
                />
              ))}
            </form>
          </section>
        )
      )}

      {saveStatus}

      <div className="wizard-nav">
        <button
          type="button"
          onClick={() => void goBack()}
          disabled={screenIndex === 0 || navigating}
        >
          Back
        </button>
        {/* Why Continue is unavailable, named rather than left to be inferred
            from a greyed-out button. The element is always present so the
            button's aria-describedby target never disappears from the
            accessibility tree; only its text comes and goes. */}
        <p id="wizard-nav-hint" className="wizard-hint">
          {screenAnswered ? "" : "Choose an option to continue."}
        </p>
        {!screenRequired && !onInspirationScreen ? (
          <button
            type="button"
            className="wizard-skip"
            onClick={() => void goSkip()}
            disabled={navigating}
          >
            Skip
          </button>
        ) : null}
        <button
          type="button"
          className="wizard-continue"
          aria-describedby="wizard-nav-hint"
          onClick={(event) => void runNavigation(() => goForward(event))}
          disabled={navigating || !screenAnswered}
        >
          {onInspirationScreen ? "Review" : "Continue"}
        </button>
      </div>
    </main>
  );
}
