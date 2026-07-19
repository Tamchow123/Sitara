"use client";

// The review screen. Before showing the draft as ready it calls the
// authoritative server-side validation endpoint and fetches the public
// configuration; a validation failure routes the user back to the errors.
// Option labels are resolved from the linked schema (never hard-coded).
//
// "Generate my concept" starts an idempotent generation job (Phase 12): one
// UUID is minted on the first deliberate click and retained in memory (never
// browser storage) for the life of the in-flight attempt, reused verbatim on
// a retry after a transport failure, and reset only once a definitive server
// outcome proves no replay is required.

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { fetchDesign, fetchPublicConfig, startDesignGeneration, validateDesignDraft } from "./api";
import { answerLabels } from "./answer-utils";
import { visibleQuestions } from "./rules";
import { resolveDesignLifecycleTarget } from "@/lib/design-lifecycle";
import type { Answers, DesignDraft, QuestionnaireSchema } from "./types";

type Props = { designId: string };

type State =
  | { phase: "loading" }
  | { phase: "redirecting" }
  | { phase: "notfound" }
  | { phase: "unavailable" }
  // The design loaded but validation could not be PERFORMED (timeout, status 0,
  // malformed response, 5xx) — distinct from a completed 400 (incomplete).
  | { phase: "validation_unavailable" }
  | { phase: "conflict" }
  | {
      phase: "ready";
      design: DesignDraft;
      schema: QuestionnaireSchema;
      valid: boolean;
      errors: Record<string, string[]>;
      generationEnabled: boolean;
    };

type SubmitState = { status: "idle" } | { status: "submitting" } | { status: "error"; message: string };

export function ReviewSummary({ designId }: Props) {
  const router = useRouter();
  const [state, setState] = useState<State>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [submit, setSubmit] = useState<SubmitState>({ status: "idle" });

  // A ref (not state) so a synchronous double click is rejected even before
  // React re-renders with the "submitting" state — state alone cannot
  // guarantee that under a rapid double click within one event loop turn.
  const submittingRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);

  const retry = useCallback(() => {
    setState({ phase: "loading" });
    setAttempt((count) => count + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const design = await fetchDesign(designId);
        if (cancelled) return;
        const target = resolveDesignLifecycleTarget(design);
        if (target.kind === "progress" || target.kind === "result") {
          setState({ phase: "redirecting" });
          router.replace(target.href);
          return;
        }
        if (target.kind === "unavailable") {
          setState({ phase: "unavailable" });
          return;
        }
        if (!design.questionnaire) {
          setState({ phase: "unavailable" });
          return;
        }
        const [validation, config] = await Promise.all([
          validateDesignDraft(designId),
          fetchPublicConfig().catch(() => null),
        ]);
        if (cancelled) return;
        const generationEnabled = config?.generation_enabled === true;
        if (validation.ok) {
          setState({
            phase: "ready",
            design,
            schema: design.questionnaire.schema,
            valid: true,
            errors: {},
            generationEnabled,
          });
          return;
        }
        // A completed HTTP 400 means the draft is genuinely incomplete; any
        // other failure means validation never ran — never conflate the two.
        if (validation.status === 400 && validation.code === "validation_failed") {
          setState({
            phase: "ready",
            design,
            schema: design.questionnaire.schema,
            valid: false,
            errors: validation.fields ?? {},
            generationEnabled,
          });
        } else {
          setState({ phase: "validation_unavailable" });
        }
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : "";
        setState({ phase: message === "not_found" ? "notfound" : "unavailable" });
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
    // router is intentionally omitted: Next.js guarantees a stable
    // reference, and including it would re-run this effect (and refetch)
    // whenever a caller's router mock is not memoised.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [designId, attempt]);

  const handleGenerate = useCallback(async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmit({ status: "submitting" });

    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = crypto.randomUUID();
    }
    const key = idempotencyKeyRef.current;

    const result = await startDesignGeneration(designId, key);

    if (result.ok) {
      idempotencyKeyRef.current = null; // confirmed success: no replay possible or needed
      router.replace(`/design/${designId}/generation/${result.data.job.id}`);
      return; // stay "submitting": we are navigating away
    }

    if (result.status === 0) {
      // Transport failure or malformed response: genuinely ambiguous whether
      // the server received the request — keep the SAME key for the retry.
      submittingRef.current = false;
      setSubmit({ status: "error", message: result.message });
      return;
    }

    // Any confirmed HTTP response is a definitive outcome: the next
    // deliberate click (if any) mints a fresh key.
    idempotencyKeyRef.current = null;

    if (result.code === "generation_in_progress" || result.code === "design_already_generated") {
      try {
        const refreshed = await fetchDesign(designId);
        const target = resolveDesignLifecycleTarget(refreshed);
        if (target.kind === "progress" || target.kind === "result") {
          router.replace(target.href);
          return; // stay "submitting": we are navigating away
        }
      } catch {
        // fall through to the controlled conflict state below
      }
      submittingRef.current = false;
      setState({ phase: "conflict" });
      return;
    }

    submittingRef.current = false;
    setSubmit({ status: "error", message: result.message });
  }, [designId, router]);

  if (state.phase === "loading" || state.phase === "redirecting") {
    return (
      <p role="status" aria-live="polite">
        Checking your design…
      </p>
    );
  }
  if (state.phase === "notfound") {
    return (
      <div role="alert">
        <h1>Design not found</h1>
        <p>This design is not available. It may belong to a different session.</p>
      </div>
    );
  }
  if (state.phase === "unavailable") {
    return (
      <div role="alert">
        <h1>Review unavailable</h1>
        <p>We could not load this design. Please try again shortly.</p>
      </div>
    );
  }
  if (state.phase === "validation_unavailable") {
    return (
      <div role="alert" className="wizard-unavailable">
        <h1>Review temporarily unavailable</h1>
        <p>We could not check your design just now. Your answers are safe.</p>
        <button type="button" onClick={retry}>
          Try again
        </button>
      </div>
    );
  }
  if (state.phase === "conflict") {
    return (
      <div role="alert">
        <h1>We couldn&apos;t confirm your generation status</h1>
        <p>Please try again in a moment.</p>
        <Link href={`/design/${designId}`}>Back to your design</Link>
      </div>
    );
  }

  const { design, schema, valid, errors, generationEnabled } = state;
  const answers = (design.answers ?? {}) as Answers;
  const visibility = visibleQuestions(schema, answers);
  const editHref = `/design/${design.id}`;
  const submitting = submit.status === "submitting";
  const canGenerate = valid && generationEnabled && !submitting;

  return (
    <main className="review">
      <h1>Review your design concept</h1>

      {!valid && (
        <div className="error-summary" role="alert">
          <h2>Some details still need attention</h2>
          <p>
            Please <Link href={editHref}>return to the questionnaire</Link> and complete the
            highlighted items before generating.
          </p>
          {Object.keys(errors).length > 0 && (
            <ul>
              {Object.entries(errors).map(([key, messages]) => (
                <li key={key}>{messages.join(" ")}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {schema.steps.map((step) => {
        const answered = step.questions.filter(
          (question) => visibility[question.id] && answerLabels(question, answers[question.id]).length > 0,
        );
        if (answered.length === 0) return null;
        return (
          <section key={step.id} aria-labelledby={`review-${step.id}`}>
            <div className="review-section-head">
              <h2 id={`review-${step.id}`}>{step.title}</h2>
              <Link href={editHref}>Edit</Link>
            </div>
            <dl>
              {answered.map((question) => (
                <div key={question.id} className="review-row">
                  <dt>{question.label}</dt>
                  <dd>{answerLabels(question, answers[question.id]).join(", ")}</dd>
                </div>
              ))}
            </dl>
          </section>
        );
      })}

      <section aria-labelledby="review-inspirations">
        <div className="review-section-head">
          <h2 id="review-inspirations">Inspiration images</h2>
          <Link href={editHref}>Edit</Link>
        </div>
        {design.selected_inspirations.length === 0 ? (
          <p>No inspiration images selected.</p>
        ) : (
          <ul className="review-inspirations">
            {design.selected_inspirations.map((selection) => (
              <li key={selection.id}>
                {selection.available && selection.asset ? (
                  <figure>
                    {/* Plain <img>, never next/image, so the backend's
                        no-store eligibility checks apply to every request. */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      className="inspiration-thumb"
                      src={selection.asset.thumbnail_url}
                      alt={selection.asset.alt_text}
                      loading="lazy"
                      width={512}
                      height={512}
                    />
                    <figcaption>
                      {selection.asset.title}
                      {selection.asset.attribution ? (
                        <span className="inspiration-attribution">
                          {selection.asset.attribution}
                        </span>
                      ) : null}
                    </figcaption>
                  </figure>
                ) : (
                  <p className="inspiration-card-unavailable">
                    This inspiration is no longer available.
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="notice">
        Sitara provides <strong>concept visualisation only</strong>. It does not produce sewing
        patterns and does not guarantee that a garment can be constructed exactly as shown.
      </p>

      <div className="wizard-nav">
        <Link href={editHref}>Back to questionnaire</Link>
        <button
          type="button"
          onClick={() => void handleGenerate()}
          disabled={!canGenerate}
          aria-describedby="generate-note"
        >
          {submitting ? "Starting…" : "Generate my concept"}
        </button>
      </div>
      <p id="generate-note" className="field-help">
        {!valid
          ? "Complete the highlighted items above before generating."
          : !generationEnabled
            ? "Concept generation is not currently available."
            : submitting
              ? "Starting your generation…"
              : "Ready to generate your concept."}
      </p>
      {submit.status === "error" && (
        <div className="generate-error" role="alert">
          <p>{submit.message}</p>
          <button type="button" onClick={() => void handleGenerate()}>
            Try again
          </button>
        </div>
      )}
    </main>
  );
}
