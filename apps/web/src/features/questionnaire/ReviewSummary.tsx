"use client";

// The review screen. Before showing the draft as ready it calls the
// authoritative server-side validation endpoint; a validation failure routes
// the user back to the errors. Option labels are resolved from the linked
// schema (never hard-coded). The "Generate my concept" button is disabled —
// generation arrives in a later phase; nothing here calls a provider.

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { fetchDesign, validateDesignDraft } from "./api";
import { answerLabels } from "./answer-utils";
import { visibleQuestions } from "./rules";
import type { Answers, DesignDraft, QuestionnaireSchema } from "./types";

type Props = { designId: string };

type State =
  | { phase: "loading" }
  | { phase: "notfound" }
  | { phase: "unavailable" }
  // The design loaded but validation could not be PERFORMED (timeout, status 0,
  // malformed response, 5xx) — distinct from a completed 400 (incomplete).
  | { phase: "validation_unavailable" }
  | {
      phase: "ready";
      design: DesignDraft;
      schema: QuestionnaireSchema;
      valid: boolean;
      errors: Record<string, string[]>;
    };

export function ReviewSummary({ designId }: Props) {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);

  const retry = useCallback(() => {
    setState({ phase: "loading" });
    setAttempt((count) => count + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const design = await fetchDesign(designId);
        if (!design.questionnaire) {
          if (!cancelled) setState({ phase: "unavailable" });
          return;
        }
        const validation = await validateDesignDraft(designId);
        if (cancelled) return;
        if (validation.ok) {
          setState({ phase: "ready", design, schema: design.questionnaire.schema, valid: true, errors: {} });
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
  }, [designId, attempt]);

  if (state.phase === "loading") {
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

  const { design, schema, valid, errors } = state;
  const answers = (design.answers ?? {}) as Answers;
  const visibility = visibleQuestions(schema, answers);
  const editHref = `/design/${design.id}`;

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
        <button type="button" disabled aria-describedby="generate-note">
          Generate my concept
        </button>
      </div>
      <p id="generate-note" className="field-help">
        Concept generation is introduced in a later phase and is not available yet.
      </p>
    </main>
  );
}
