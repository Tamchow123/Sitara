"use client";

// The single-round refinement request form (Phase 14 §26-28). Mirrors
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
} from "./refinement-options";
import {
  REFINEMENT_SUBMIT_CODES_REQUIRING_RECHECK,
  refinementSubmitErrorMessage,
} from "./refinement-errors";
import { startDesignRefinement } from "@/lib/api";
import type { ChangeType } from "@/lib/api";

type Props = {
  designId: string;
  sourceVersionId: string;
  onRequiresRecheck?: () => void;
};

const DRIFT_WARNING =
  "Refinement creates a fresh AI-generated image. Sitara will ask for only your " +
  "selected change, but the pose, composition, face, garment details and " +
  "embroidery placement may still differ substantially. Reusing the original " +
  "seed is only a continuity aid, not a guarantee.";

type SubmitState = { status: "idle" } | { status: "submitting" } | { status: "error"; message: string };

export function RefinementPanel({ designId, sourceVersionId, onRequiresRecheck }: Props) {
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
      setSubmit({ status: "error", message: result.message });
      return;
    }

    // Any confirmed HTTP response is a definitive outcome: the next
    // deliberate click (if any) mints a fresh key.
    idempotencyKeyRef.current = null;
    submittingRef.current = false;
    setSubmit({ status: "error", message: refinementSubmitErrorMessage(result.code, result.message) });

    if (REFINEMENT_SUBMIT_CODES_REQUIRING_RECHECK.has(result.code)) {
      onRequiresRecheck?.();
    }
  }, [changeType, noteValid, acknowledged, designId, sourceVersionId, note, router, onRequiresRecheck]);

  const remaining = REFINEMENT_NOTE_MAX_LENGTH - note.length;

  return (
    <section className="refinement-panel" aria-labelledby="refinement-heading">
      <h2 id="refinement-heading">Refine this concept</h2>
      <p>You may request exactly one change to your design brief, then generate a fresh concept.</p>

      <fieldset className="refinement-chip-group">
        <legend>Choose one change</legend>
        <div role="radiogroup" aria-label="Choose one change" className="refinement-chips">
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
                onChange={() => setChangeType(option.value)}
              />
              {option.label}
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

      <div className="refinement-drift-warning" role="note" aria-label="Refinement disclaimer">
        <p>{DRIFT_WARNING}</p>
        <label className="refinement-ack">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          I understand the refined image is a new generation and may differ substantially from the
          original.
        </label>
      </div>

      <div className="refinement-actions">
        <button type="button" onClick={() => void handleSubmit()} disabled={!canSubmit}>
          {submitting ? "Starting…" : "Request refinement"}
        </button>
      </div>
      <p role="status" aria-live="polite" className="field-help">
        {submitting
          ? "Starting your refinement…"
          : changeType === null
            ? "Choose one change to enable refinement."
            : !acknowledged
              ? "Please acknowledge the disclaimer above before submitting."
              : "Ready to request your refinement."}
      </p>
      {submit.status === "error" && (
        <div className="refinement-error" role="alert">
          <p>{submit.message}</p>
          <button type="button" onClick={() => void handleSubmit()}>
            Try again
          </button>
        </div>
      )}
    </section>
  );
}
