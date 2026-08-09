"use client";

// One `role="status"` pill covering all five save states (§14).
//
// The system has no red, and errors on this screen stay in the terracotta ramp —
// `--color-bad` remains in use for form validation elsewhere and is deliberately
// not introduced here. A failing save is recoverable and the notes are still on
// screen, so alarm colour would overstate it.

import type { SaveStatus } from "./useAutosave";

type Props = {
  save: SaveStatus;
  onRetry: () => void;
  onReview: () => void;
};

export function SavePill({ save, onRetry, onReview }: Props) {
  return (
    <p className={`annotation-save-pill is-${save.status}`} role="status">
      {save.status === "saved" && (
        <>
          <TickGlyph />
          <span>Saved</span>
        </>
      )}

      {save.status === "saving" && (
        <>
          <span className="annotation-spinner" aria-hidden="true" />
          <span>Saving…</span>
        </>
      )}

      {save.status === "unsaved" && <span>Unsaved changes</span>}

      {save.status === "failed" && (
        <>
          <WarnGlyph />
          <span>Couldn&rsquo;t save</span>
          <button type="button" className="annotation-pill-action" onClick={onRetry}>
            Retry
          </button>
        </>
      )}

      {save.status === "conflict" && (
        <>
          <span>Conflict</span>
          <button type="button" className="annotation-pill-action" onClick={onReview}>
            Reload
          </button>
        </>
      )}
    </p>
  );
}

function TickGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <path
        d="M5 13l4 4L19 7"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function WarnGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <path d="M12 3l9 16H3l9-16Zm0 6v5m0 2.5v1.2" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
    </svg>
  );
}
