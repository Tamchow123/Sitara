"use client";

// The constrained refinement request form — one edit per request, and since
// ADR 0029 up to three requests per concept (Phase 14 §26-28). Mirrors
// ReviewSummary's idempotency discipline exactly: one crypto.randomUUID() key
// minted on the first deliberate submit, retained in a ref (never browser
// storage) across a transport-failure retry, reset only on a definitive
// server outcome. A synchronous ref (not state) rejects a same-tick double
// click before React re-renders with the "submitting" state.

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  REFINEMENT_CHANGE_TYPE_OPTIONS,
  REFINEMENT_NOTE_MAX_LENGTH,
  isNoteWithinLimit,
  refinementsLeftLabel,
} from "./refinement-options";
import {
  REFINEMENT_SUBMIT_CODES_REQUIRING_RECHECK,
  REFINEMENT_SUBMIT_TERMINAL_CODES,
  refinementSubmitErrorMessage,
} from "./refinement-errors";
import { startDesignRefinement } from "@/lib/api";
import type { ChangeType } from "@/lib/api";

type Props = {
  designId: string;
  sourceVersionId: string;
  isDemo?: boolean;
  /** The DESIGN's remaining budget, from the server. Always at least 1 here —
   *  the section only mounts this form when a round is left. */
  refinementsRemaining: number;
  onRequiresRecheck?: () => void;
};

const DRIFT_WARNING =
  "Refinement creates a fresh AI-generated image. Sitara will ask for only your " +
  "selected change, but the pose, composition, face, garment details and " +
  "embroidery placement may still differ substantially. Reusing the original " +
  "seed is only a continuity aid, not a guarantee.";

const DEMO_DRIFT_WARNING =
  "In demo mode, refinement updates your deterministic design brief within the selected " +
  "category only. Another curated image may be selected to match the updated brief — the " +
  "image itself is not edited, and the image you are refining is never sent anywhere. Visual " +
  "differences from the concept you are refining may still be substantial.";

type SubmitState =
  | { status: "idle" }
  | { status: "submitting" }
  // See REFINEMENT_SUBMIT_TERMINAL_CODES: false where an immediate retry
  // cannot succeed, so no retry control is rendered.
  | { status: "error"; message: string; retryable: boolean };

export function RefinementPanel({
  designId,
  sourceVersionId,
  isDemo = false,
  refinementsRemaining,
  onRequiresRecheck,
}: Props) {
  const router = useRouter();
  const [changeType, setChangeType] = useState<ChangeType | null>(null);
  const [note, setNote] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [submit, setSubmit] = useState<SubmitState>({ status: "idle" });

  const submittingRef = useRef(false);
  const idempotencyKeyRef = useRef<string | null>(null);

  const noteValid = isNoteWithinLimit(note);
  const submitting = submit.status === "submitting";
  const canSubmit = changeType !== null && noteValid && acknowledged && !submitting;

  const handleSubmit = useCallback(async () => {
    if (submittingRef.current) return;
    if (changeType === null || !noteValid || !acknowledged) return;
    submittingRef.current = true;
    setSubmit({ status: "submitting" });

    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = crypto.randomUUID();
    }
    const key = idempotencyKeyRef.current;

    const result = await startDesignRefinement(
      designId,
      { source_version_id: sourceVersionId, change_type: changeType, note },
      key,
    );

    if (result.ok) {
      idempotencyKeyRef.current = null; // confirmed success: no replay possible or needed
      router.replace(
        `/design/${designId}/generation/${result.data.job.id}?from=${encodeURIComponent(
          sourceVersionId,
        )}`,
      );
      return; // stay "submitting": we are navigating away
    }

    if (result.status === 0) {
      // Transport failure or malformed response: genuinely ambiguous whether
      // the server received the request — keep the SAME key for the retry.
      submittingRef.current = false;
      setSubmit({ status: "error", message: result.message, retryable: true });
      return;
    }

    // Any confirmed HTTP response is a definitive outcome: the next
    // deliberate click (if any) mints a fresh key.
    idempotencyKeyRef.current = null;
    submittingRef.current = false;
    setSubmit({
      status: "error",
      message: refinementSubmitErrorMessage(result.code, result.message),
      retryable: !REFINEMENT_SUBMIT_TERMINAL_CODES.has(result.code),
    });

    if (REFINEMENT_SUBMIT_CODES_REQUIRING_RECHECK.has(result.code)) {
      onRequiresRecheck?.();
    }
  }, [changeType, noteValid, acknowledged, designId, sourceVersionId, note, router, onRequiresRecheck]);

  const remaining = REFINEMENT_NOTE_MAX_LENGTH - note.length;

  return (
    <section className="refinement-panel" id="refine-concept" aria-labelledby="refinement-heading">
      {/* The Amendments screen's kicker counts refinements, and now it counts a
          real number: the server's own remaining budget, not a constant
          restated here. The count is the design's, not this version's — three
          rounds are shared across the whole chain. */}
      <p className="kicker">
        Refine this concept · {refinementsLeftLabel(refinementsRemaining)}
      </p>
      <h2 id="refinement-heading">What would you change?</h2>
      <p className="refinement-lede">
        You may request one change to your design brief, then generate a fresh concept. Sitara
        holds everything you have not asked to change as steady as it can.
      </p>

      <fieldset className="refinement-chip-group">
        <legend>Choose one change</legend>
        {/* Radios, not the prototype's aria-pressed toggle buttons: exactly one
            of these may hold, and a radio group is what says so to assistive
            technology and to the keyboard (arrow keys move within the group).
            The card look is styling on top of that, not a replacement for it. */}
        {/* Each chip names the answer it will move, not just the category
            (ADR 0028). Until this phase a refinement could not alter the image
            at all, so a promise about what would change was one the product
            could not keep; now it can, and "Changes the fabrics you chose" is
            what makes a choice between seven chips meaningful.

            The radio is named by the category span and DESCRIBED by the effect
            span, explicitly. Without the explicit aria-labelledby the wrapping
            label would fold both spans into the accessible name, and the
            aria-describedby would then read the effect out a second time — the
            same sentence twice per option, seven times over. */}
        <div className="refinement-chips">
          {REFINEMENT_CHANGE_TYPE_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={
                changeType === option.value ? "refinement-chip refinement-chip-selected" : "refinement-chip"
              }
            >
              <input
                type="radio"
                name="refinement-change-type"
                value={option.value}
                checked={changeType === option.value}
                aria-labelledby={`refinement-label-${option.value}`}
                aria-describedby={`refinement-effect-${option.value}`}
                onChange={() => setChangeType(option.value)}
              />
              <span className="refinement-chip-label" id={`refinement-label-${option.value}`}>
                {option.label}
              </span>
              <span className="refinement-chip-effect" id={`refinement-effect-${option.value}`}>
                {option.effect}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="field refinement-note-field">
        <label htmlFor="refinement-note" className="field-label">
          Optional note
        </label>
        <textarea
          id="refinement-note"
          className="field-textarea refinement-note"
          value={note}
          maxLength={REFINEMENT_NOTE_MAX_LENGTH}
          aria-describedby="refinement-note-help refinement-note-count"
          onChange={(event) => setNote(event.target.value)}
        />
        <p id="refinement-note-help" className="field-help">
          A short preference for your selected change. This is not an open-ended chat box — Sitara
          only applies the one allowed category above.
        </p>
        <p id="refinement-note-count" className="refinement-note-count">
          {remaining} characters remaining
        </p>
        {!noteValid && (
          <p className="field-error" role="alert">
            Please shorten your note to {REFINEMENT_NOTE_MAX_LENGTH} characters or fewer.
          </p>
        )}
      </div>

      <div
        id="refinement-disclaimer"
        className="refinement-drift-warning"
        role="note"
        aria-label="Refinement disclaimer"
      >
        <p>{isDemo ? DEMO_DRIFT_WARNING : DRIFT_WARNING}</p>
        <label className="refinement-ack">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          {isDemo
            ? "I understand the refined image is selected from the demo pack and may differ substantially from the original."
            : "I understand the refined image is a new generation and may differ substantially from the original."}
        </label>
      </div>

      <div className="refinement-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void handleSubmit()}
          disabled={!canSubmit}
          aria-busy={submitting || undefined}
          aria-describedby="refinement-disclaimer refinement-submit-note"
        >
          {submitting ? "Starting…" : "Request refinement"}
        </button>
        {/* The handoff's hint sits beside the button and always carries the
            reason the button is where it is — a disabled control with no
            explanation is the state this line exists to prevent. */}
        <p id="refinement-submit-note" role="status" aria-live="polite" className="field-help">
          {submitting
            ? "Starting your refinement…"
            : changeType === null
              ? "Choose one change to enable refinement."
              : !acknowledged
                ? "Please acknowledge the disclaimer above before submitting."
                : "Ready to request your refinement."}
        </p>
      </div>
      {submit.status === "error" && (
        <div className="refinement-error" role="alert">
          <p>{submit.message}</p>
          {submit.retryable && (
            <button type="button" className="btn btn-secondary" onClick={() => void handleSubmit()}>
              Try again
            </button>
          )}
        </div>
      )}
    </section>
  );
}
