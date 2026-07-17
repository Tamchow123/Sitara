"use client";

// The schema-driven questionnaire wizard. It fetches the active questionnaire
// through the generated GET client, renders steps/questions/options from the
// schema, applies show/hide/require/restrict rules immediately, clears stale
// answers, validates the current visible step before advancing, and persists
// progress to the private Design through the CSRF-aware API — never to browser
// storage. The Design is created on the first successful save (not on page
// view); resume reconstructs the wizard from the persisted answers and the
// design's linked questionnaire. Backend validation stays authoritative.

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  createDesignDraft,
  fetchActiveQuestionnaire,
  fetchCatalogue,
  fetchDesign,
  updateDesignDraft,
} from "./api";
import { InspirationPicker } from "./InspirationPicker";
import { QuestionField } from "./QuestionField";
import { allowedOptions, questionsById, visibleQuestions } from "./rules";
import { clearStaleAnswers, resumeStepIndex, visibleStepQuestions } from "./answer-utils";
import { validateAnswers } from "./validation";
import type {
  Answers,
  AnswerValue,
  PublicAsset,
  QuestionnaireSchema,
} from "./types";

const MAX_INSPIRATIONS = 3;
const TEXT_DEBOUNCE_MS = 600;

type LoadState = "loading" | "ready" | "unavailable" | "notfound";
type SaveState = "idle" | "saving" | "saved" | "error";

type Props = { initialDesignId?: string };

export function QuestionnaireWizard({ initialDesignId }: Props) {
  const router = useRouter();

  const [load, setLoad] = useState<LoadState>("loading");
  const [reloadCounter, setReloadCounter] = useState(0);
  const [schema, setSchema] = useState<QuestionnaireSchema | null>(null);
  const [versionId, setVersionId] = useState<string>("");
  const [designId, setDesignId] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Answers>({});
  const [selection, setSelection] = useState<string[]>([]);
  const [catalogue, setCatalogue] = useState<PublicAsset[] | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [errorTick, setErrorTick] = useState(0);

  const answersRef = useRef<Answers>({});
  const selectionRef = useRef<string[]>([]);
  const textTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const errorSummaryRef = useRef<HTMLDivElement>(null);

  const setAnswersSynced = useCallback((next: Answers) => {
    answersRef.current = next;
    setAnswers(next);
  }, []);
  const setSelectionSynced = useCallback((next: string[]) => {
    selectionRef.current = next;
    setSelection(next);
  }, []);

  // -- Load / resume --------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    setLoad("loading");
    async function loadWizard() {
      try {
        if (initialDesignId) {
          const design = await fetchDesign(initialDesignId);
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
          setDesignId(design.id);
          setAnswersSynced(loadedAnswers);
          setSelectionSynced(design.selected_inspirations.map((entry) => entry.id));
          setStepIndex(Math.min(resumeStepIndex(loadedSchema, loadedAnswers), loadedSchema.steps.length));
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
  }, [initialDesignId, reloadCounter, setAnswersSynced, setSelectionSynced]);

  // -- Catalogue (loaded lazily when the inspiration step is reached) --------
  const onInspirationStep = schema !== null && stepIndex === schema.steps.length;
  useEffect(() => {
    if (load !== "ready" || !onInspirationStep || catalogue !== null) return;
    let cancelled = false;
    fetchCatalogue()
      .then((response) => {
        if (!cancelled) setCatalogue(response.assets);
      })
      .catch(() => {
        if (!cancelled) setCatalogue([]);
      });
    return () => {
      cancelled = true;
    };
  }, [load, onInspirationStep, catalogue]);

  useEffect(() => {
    if (Object.keys(fieldErrors).length > 0) errorSummaryRef.current?.focus();
  }, [errorTick, fieldErrors]);

  // -- Persistence ----------------------------------------------------------
  const persist = useCallback(
    async (patch: { answers?: Answers; inspiration_asset_ids?: string[] }): Promise<boolean> => {
      setSaveState("saving");
      setSaveError(null);
      const body: Record<string, unknown> = {};
      if (patch.answers !== undefined) body.answers = patch.answers;
      if (patch.inspiration_asset_ids !== undefined) {
        body.inspiration_asset_ids = patch.inspiration_asset_ids;
      }
      const currentDesignId = designId;
      const result =
        currentDesignId === null
          ? await createDesignDraft({ questionnaire_version_id: versionId, ...body })
          : await updateDesignDraft(currentDesignId, body);
      if (result.ok) {
        setSaveState("saved");
        setFieldErrors({});
        if (currentDesignId === null) {
          setDesignId(result.data.id);
          router.replace(`/design/${result.data.id}`);
        }
        return true;
      }
      setSaveState("error");
      setSaveError(result.message);
      if (result.fields) setFieldErrors(result.fields);
      return false;
    },
    [designId, versionId, router],
  );

  const flushText = useCallback(() => {
    if (textTimer.current) clearTimeout(textTimer.current);
    textTimer.current = undefined;
  }, []);

  // -- Answer changes -------------------------------------------------------
  const onAnswerChange = useCallback(
    (questionId: string, value: AnswerValue) => {
      if (!schema) return;
      const next = clearStaleAnswers(schema, { ...answersRef.current, [questionId]: value });
      setAnswersSynced(next);
      const question = questionsById(schema)[questionId];
      if (question?.type === "text") {
        if (textTimer.current) clearTimeout(textTimer.current);
        textTimer.current = setTimeout(() => {
          void persist({ answers: answersRef.current });
        }, TEXT_DEBOUNCE_MS);
      } else {
        void persist({ answers: next });
      }
    },
    [schema, persist, setAnswersSynced],
  );

  const onAnswerBlur = useCallback(
    (questionId: string) => {
      if (!schema) return;
      const question = questionsById(schema)[questionId];
      if (question?.type === "text") {
        flushText();
        void persist({ answers: answersRef.current });
      }
    },
    [schema, persist, flushText],
  );

  const onSelectionChange = useCallback(
    (ids: string[]) => {
      setSelectionSynced(ids);
      void persist({ inspiration_asset_ids: ids });
    },
    [persist, setSelectionSynced],
  );

  // -- Navigation -----------------------------------------------------------
  const validateCurrentStep = useCallback((): boolean => {
    if (!schema || onInspirationStep) return true;
    const step = schema.steps[stepIndex];
    const result = validateAnswers(schema, answersRef.current, true);
    const stepIds = new Set(visibleStepQuestions(schema, step, answersRef.current).map((q) => q.id));
    const stepErrors: Record<string, string[]> = {};
    for (const [key, messages] of Object.entries(result.errors)) {
      if (stepIds.has(key)) stepErrors[key] = messages;
    }
    setFieldErrors(stepErrors);
    if (Object.keys(stepErrors).length > 0) {
      setErrorTick((tick) => tick + 1);
      return false;
    }
    return true;
  }, [schema, stepIndex, onInspirationStep]);

  const goBack = useCallback(() => {
    flushText();
    setFieldErrors({});
    setStepIndex((index) => Math.max(0, index - 1));
  }, [flushText]);

  const goForward = useCallback(async () => {
    if (!schema) return;
    flushText();
    if (!validateCurrentStep()) return;
    // Flush any pending text save before moving on.
    if (!onInspirationStep) {
      await persist({ answers: answersRef.current });
    }
    if (onInspirationStep) {
      if (designId) router.push(`/design/${designId}/review`);
      return;
    }
    setStepIndex((index) => Math.min(index + 1, schema.steps.length));
  }, [schema, onInspirationStep, validateCurrentStep, persist, designId, router, flushText]);

  // -- Render ---------------------------------------------------------------
  if (load === "loading") {
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
  if (load === "unavailable" || schema === null) {
    return (
      <div role="alert" className="wizard-unavailable">
        <p>The questionnaire is temporarily unavailable.</p>
        <button type="button" onClick={() => setReloadCounter((count) => count + 1)}>
          Try again
        </button>
      </div>
    );
  }

  const totalSteps = schema.steps.length + 1; // + inspiration step
  const humanStep = stepIndex + 1;
  const visibility = visibleQuestions(schema, answers);
  const allowed = allowedOptions(schema, answers);
  const index = questionsById(schema);
  const errorEntries = Object.entries(fieldErrors);

  const saveStatus = (
    <p className="save-status" role="status" aria-live="polite">
      {saveState === "saving" && "Saving…"}
      {saveState === "saved" && "Saved"}
      {saveState === "error" && (
        <span className="save-error">
          {saveError ?? "Could not save."}{" "}
          <button type="button" onClick={() => void persist({ answers: answersRef.current })}>
            Retry
          </button>
        </span>
      )}
    </p>
  );

  return (
    <main className="wizard">
      <p className="wizard-progress">
        Step {humanStep} of {totalSteps}
      </p>

      {errorEntries.length > 0 && (
        <div
          className="error-summary"
          role="alert"
          tabIndex={-1}
          ref={errorSummaryRef}
          aria-label="There is a problem"
        >
          <h2>Please review your answers</h2>
          <ul>
            {errorEntries.map(([key, messages]) => (
              <li key={key}>
                {index[key] ? index[key].label : key}: {messages.join(" ")}
              </li>
            ))}
          </ul>
        </div>
      )}

      {onInspirationStep ? (
        <section aria-labelledby="inspiration-heading">
          <h1 id="inspiration-heading">Inspiration images</h1>
          {catalogue === null ? (
            <p role="status" aria-live="polite">
              Loading inspiration images…
            </p>
          ) : (
            <InspirationPicker
              assets={catalogue}
              selection={selection}
              max={MAX_INSPIRATIONS}
              onChange={onSelectionChange}
            />
          )}
        </section>
      ) : (
        <section aria-labelledby="step-heading">
          <h1 id="step-heading">{schema.steps[stepIndex].title}</h1>
          {schema.steps[stepIndex].description ? (
            <p className="step-description">{schema.steps[stepIndex].description}</p>
          ) : null}
          <form onSubmit={(event) => event.preventDefault()}>
            {schema.steps[stepIndex].questions
              .filter((question) => visibility[question.id])
              .map((question) => (
                <QuestionField
                  key={question.id}
                  question={question}
                  value={answers[question.id]}
                  error={fieldErrors[question.id]?.join(" ")}
                  allowed={allowed[question.id] ?? new Set<string>()}
                  onChange={(value) => onAnswerChange(question.id, value)}
                  onBlur={() => onAnswerBlur(question.id)}
                />
              ))}
          </form>
        </section>
      )}

      {saveStatus}

      <div className="wizard-nav">
        <button type="button" onClick={goBack} disabled={stepIndex === 0}>
          Back
        </button>
        <button type="button" onClick={() => void goForward()}>
          {onInspirationStep ? "Review" : "Continue"}
        </button>
      </div>
    </main>
  );
}
