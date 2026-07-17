"use client";

// The schema-driven questionnaire wizard. It fetches the active questionnaire
// through the generated GET client, renders steps/questions/options from the
// schema, applies show/hide/require/restrict rules immediately, clears stale
// answers, validates the current visible step through React Hook Form + a
// derived Zod resolver, and persists progress to the private Design through a
// single-flight save coordinator (never to browser storage). The Design is
// created on the first successful save (not on page view); resume reconstructs
// the wizard from the persisted answers and the design's linked questionnaire.
// Backend validation stays authoritative.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { useRouter } from "next/navigation";

import { fetchActiveQuestionnaire, fetchCatalogue, fetchDesign } from "./api";
import { InspirationPicker } from "./InspirationPicker";
import { QuestionField } from "./QuestionField";
import { allowedOptions, questionsById, visibleQuestions } from "./rules";
import { clearStaleAnswers, resumeStepIndex } from "./answer-utils";
import { createStepResolver } from "./validation";
import { useDraftSaver } from "./use-draft-saver";
import type { Answers, AnswerValue, PublicAsset, QuestionnaireSchema, Step } from "./types";

const MAX_INSPIRATIONS = 3;

type LoadState = "loading" | "ready" | "unavailable" | "notfound";
type CatalogueState = {
  status: "idle" | "loading" | "ready" | "unavailable";
  assets: PublicAsset[];
};

type Props = { initialDesignId?: string };
type StepValues = Record<string, AnswerValue>;

export function QuestionnaireWizard({ initialDesignId }: Props) {
  const router = useRouter();

  const [load, setLoad] = useState<LoadState>("loading");
  const [reloadCounter, setReloadCounter] = useState(0);
  const [schema, setSchema] = useState<QuestionnaireSchema | null>(null);
  const [versionId, setVersionId] = useState<string>("");
  const [answers, setAnswers] = useState<Answers>({});
  const [selection, setSelection] = useState<string[]>([]);
  const [catalogue, setCatalogue] = useState<CatalogueState>({ status: "idle", assets: [] });
  const [stepIndex, setStepIndex] = useState(0);
  const [errorTick, setErrorTick] = useState(0);

  const answersRef = useRef<Answers>({});
  const schemaRef = useRef<QuestionnaireSchema | null>(null);
  const stepIndexRef = useRef(0);
  const errorSummaryRef = useRef<HTMLDivElement>(null);

  const setAnswersSynced = useCallback((next: Answers) => {
    answersRef.current = next;
    setAnswers(next);
  }, []);

  useEffect(() => {
    schemaRef.current = schema;
  }, [schema]);
  useEffect(() => {
    stepIndexRef.current = stepIndex;
  }, [stepIndex]);

  const saver = useDraftSaver({
    versionId,
    onCreated: (design) => router.replace(`/design/${design.id}`),
  });
  const { flush, retry, save, saveText, adopt } = saver;

  // React Hook Form drives the CURRENT visible step; the cross-step Answers
  // object remains the source of truth (RHF mirrors it through `values`). The
  // resolver reads the latest step/answers lazily so visibility, requiredness
  // and option restrictions always reflect the newest answers.
  const stepResolver = useMemo(
    () =>
      createStepResolver(() => {
        const s = schemaRef.current;
        const idx = stepIndexRef.current;
        const step = s && idx < s.steps.length ? s.steps[idx] : null;
        return { schema: s, step, answers: answersRef.current };
      }),
    [],
  );
  const form = useForm<StepValues>({
    values: answers as StepValues,
    resolver: stepResolver,
    mode: "onSubmit",
  });

  const onInspirationStep = schema !== null && stepIndex === schema.steps.length;

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
          adopt(design.id);
          setAnswersSynced(loadedAnswers);
          setSelection(design.selected_inspirations.map((entry) => entry.id));
          setStepIndex(
            Math.min(resumeStepIndex(loadedSchema, loadedAnswers), loadedSchema.steps.length),
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
  }, [initialDesignId, reloadCounter, setAnswersSynced, adopt]);

  // -- Catalogue (loaded lazily; empty is valid, failure is distinct) --------
  useEffect(() => {
    if (load !== "ready" || !onInspirationStep || catalogue.status !== "idle") return;
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
  }, [load, onInspirationStep, catalogue.status]);

  useEffect(() => {
    if (errorTick > 0) errorSummaryRef.current?.focus();
  }, [errorTick]);

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
  const goBack = useCallback(async () => {
    form.clearErrors();
    // Flush pending work before leaving; even if it fails the local values stay
    // visible and Retry is offered, so Back still returns to the prior step.
    await flush();
    setStepIndex((index) => Math.max(0, index - 1));
  }, [flush, form]);

  const goForward = useMemo(
    () =>
      form.handleSubmit(
        async () => {
          const saved = await flush();
          if (!saved) {
            // Required save failed — do not advance; error + Retry are shown.
            setErrorTick((tick) => tick + 1);
            return;
          }
          if (onInspirationStep) {
            if (saver.designId) router.push(`/design/${saver.designId}/review`);
            return;
          }
          setStepIndex((index) => index + 1);
        },
        () => setErrorTick((tick) => tick + 1),
      ),
    [form, flush, onInspirationStep, saver.designId, router],
  );

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
  const visibility = visibleQuestions(schema, answers);
  const allowed = allowedOptions(schema, answers);
  const index = questionsById(schema);

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

  const currentStep: Step | null = onInspirationStep ? null : schema.steps[stepIndex];

  return (
    <main className="wizard">
      <p className="wizard-progress">
        Step {stepIndex + 1} of {totalSteps}
      </p>

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

      {onInspirationStep ? (
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
                onClick={() => setCatalogue({ status: "idle", assets: [] })}
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
            />
          )}
        </section>
      ) : (
        currentStep && (
          <section aria-labelledby="step-heading">
            <h1 id="step-heading">{currentStep.title}</h1>
            {currentStep.description ? (
              <p className="step-description">{currentStep.description}</p>
            ) : null}
            <form onSubmit={(event) => event.preventDefault()}>
              {currentStep.questions
                .filter((question) => visibility[question.id])
                .map((question) => (
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
        <button type="button" onClick={() => void goBack()} disabled={stepIndex === 0}>
          Back
        </button>
        <button type="button" onClick={(event) => void goForward(event)}>
          {onInspirationStep ? "Review" : "Continue"}
        </button>
      </div>
    </main>
  );
}
