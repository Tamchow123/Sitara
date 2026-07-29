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
import { generationSubmitErrorMessage } from "@/features/generation/submit-errors";
import { inspirationUploadImageUrl, type PublicConfig } from "@/lib/api";
import type { Answers, DesignDraft, Question, QuestionnaireSchema } from "./types";

type Props = { designId: string };

// What an unanswered row says. The handoff's phrasing, kept verbatim: it frames
// an absent answer as a deliberate gift of freedom rather than an omission.
const UNANSWERED_TEXT = "Left to Sitara's imagination";

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
      demoMode: boolean;
      generationMode: PublicConfig["generation_mode"] | null;
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
        const demoMode = config?.demo_mode === true;
        const generationMode = config?.generation_mode ?? null;
        if (validation.ok) {
          setState({
            phase: "ready",
            design,
            schema: design.questionnaire.schema,
            valid: true,
            errors: {},
            generationEnabled,
            demoMode,
            generationMode,
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
            demoMode,
            generationMode,
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

    // Every other confirmed failure — including the Phase 16 admission states
    // (live_generation_disabled, generation_limit_reached,
    // live_generation_budget_exhausted) and a temporary admission outage — is
    // shown as a clear, non-technical, terminal message. The draft is fully
    // preserved on this review page and nothing retries automatically; the user
    // may deliberately try again later.
    submittingRef.current = false;
    setSubmit({
      status: "error",
      message: generationSubmitErrorMessage(result.code, result.message),
    });
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

  const { design, schema, valid, errors, generationEnabled, demoMode, generationMode } = state;
  // Read straight from the design the server returned — a resumed review must
  // show the uploads that are actually attached, never a client-side guess.
  const uploads = design.inspiration_uploads ?? [];
  const answers = (design.answers ?? {}) as Answers;
  const visibility = visibleQuestions(schema, answers);
  const editHref = `/design/${design.id}`;
  const submitting = submit.status === "submitting";
  const canGenerate = valid && generationEnabled && !submitting;
  const demoAssetsUnavailable = demoMode && generationMode === "unavailable";
  const describedBy = demoMode ? "generate-note demo-disclosure" : "generate-note";

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
        // EVERY visible question gets a row (Phase 16B): an answered one shows
        // its labels, an unanswered one says so in the handoff's own words
        // rather than vanishing. A silently missing row reads as "I never
        // asked" — the opposite of the reassurance this screen is for.
        const rows = step.questions
          .filter((question) => visibility[question.id])
          .map((question: Question) => {
            const labels = answerLabels(question, answers[question.id]);
            return {
              question,
              text: labels.length > 0 ? labels.join(", ") : UNANSWERED_TEXT,
              answered: labels.length > 0,
            };
          });
        if (rows.length === 0) return null;
        return (
          <section key={step.id} aria-labelledby={`review-${step.id}`}>
            <div className="review-section-head">
              <h2 id={`review-${step.id}`}>{step.title}</h2>
            </div>
            <dl>
              {rows.map(({ question, text, answered }) => (
                <div key={question.id} className="review-row">
                  <dt>{question.label}</dt>
                  <dd className={answered ? undefined : "review-unanswered"}>{text}</dd>
                  {/* Per-row Edit, deep-linked to that one question's screen —
                      the wizard resolves ?q= to a screen index and still
                      refuses to skip past what has been reached. */}
                  <Link
                    className="review-edit"
                    href={`${editHref}?q=${encodeURIComponent(question.id)}`}
                    // Named for assistive technology through aria-label rather
                    // than a visually-hidden span, so the question's text
                    // appears exactly once in the row.
                    aria-label={`Edit ${question.label}`}
                  >
                    Edit
                  </Link>
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
        {design.selected_inspirations.length === 0 && uploads.length === 0 ? (
          <p>No inspiration images selected.</p>
        ) : (
          <>
            <p className="field-help">
              Your references guide compatible details only — your garment, ceremony,
              colour, embellishment and coverage answers always take priority. The images
              below are sent to the external AI image provider that draws your concept, and
              the concept will not be an exact copy of any of them.
            </p>
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
            {uploads.length > 0 && (
              <>
                <h3 className="review-uploads-heading">Your own photographs</h3>
                <ul className="review-inspirations">
                  {uploads.map((upload, index) => (
                    <li key={upload.id}>
                      <figure>
                        {/* Plain <img>, never next/image: ownership-checked,
                            no-store bytes must not be proxied or cached. */}
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          className="upload-thumb"
                          src={inspirationUploadImageUrl(design.id, upload.id)}
                          alt={`Your uploaded inspiration image ${index + 1}`}
                          width={upload.width}
                          height={upload.height}
                        />
                        <figcaption>Uploaded by you</figcaption>
                      </figure>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </section>

      <p className="notice">
        Sitara provides <strong>concept visualisation only</strong>. It does not produce sewing
        patterns and does not guarantee that a garment can be constructed exactly as shown.
      </p>

      {demoMode && (
        <div id="demo-disclosure" className="demo-disclosure" role="note" aria-label="Demo disclosure">
          <p>
            In demo mode, your structured selections determine a deterministic design brief, and
            any approved inspiration descriptions may influence that brief. Free-text
            interpretation is limited in demo mode. The visual is selected from a curated pack of
            pre-generated concepts and may not show every detail in the brief.
          </p>
        </div>
      )}

      <div className="wizard-nav">
        <Link href={editHref}>Back to questionnaire</Link>
        <button
          type="button"
          onClick={() => void handleGenerate()}
          disabled={!canGenerate}
          aria-describedby={describedBy}
        >
          {submitting ? "Starting…" : "Generate my concept"}
        </button>
      </div>
      <p id="generate-note" className="field-help">
        {!valid
          ? "Complete the highlighted items above before generating."
          : !generationEnabled
            ? demoAssetsUnavailable
              ? "Demo generation is temporarily unavailable because its visual library is not ready."
              : "Concept generation is not currently available."
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
