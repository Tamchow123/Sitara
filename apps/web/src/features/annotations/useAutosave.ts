"use client";

// Autosave, coalescing and conflict handling (§14).
//
// Three rules drive the whole shape of this file:
//
//  1. ONE save in flight. Annotation writes are whole-document replacements
//     guarded by `expected_revision`, so two overlapping requests would race and
//     the loser would 409 against a revision its own sibling had just moved.
//  2. Later edits COALESCE. Everything typed while a save is in flight becomes
//     one follow-up write, not one per keystroke.
//  3. A failure or a conflict SUSPENDS autosave until the user acts. Without
//     that, a flaky connection re-fires a doomed write every debounce tick and
//     re-opens the conflict modal mid-edit — the wedged loop §14 exists to
//     prevent. Local edits keep accumulating meanwhile, which is safe precisely
//     because they are never silently discarded.
//
// Nothing here writes to localStorage, sessionStorage or IndexedDB. The document
// is memory-only; a refresh is meant to load the server's copy.

import { useCallback, useEffect, useRef, useState } from "react";

import { AUTOSAVE_DEBOUNCE_MS } from "./limits";
import { isDirty, itemsEqual, type EditorAction, type EditorState } from "./reducer";
import { fetchAnnotations, saveAnnotations } from "@/lib/api";
import type { AnnotationItem } from "@/lib/api";

export type SaveStatus =
  | { status: "saved" }
  | { status: "saving" }
  | { status: "unsaved" }
  | { status: "failed"; message: string }
  | { status: "conflict"; message: string };

type Params = {
  designId: string;
  versionId: string;
  imageWidth: number;
  imageHeight: number;
  schemaVersion: number;
  state: EditorState;
  dispatch: (action: EditorAction) => void;
  /** False until the first document has loaded, so nothing is written over it. */
  ready: boolean;
};

export type Autosave = {
  save: SaveStatus;
  /** True when there are edits the server has not confirmed. */
  dirty: boolean;
  /**
   * Save now rather than waiting out the debounce — used on editor blur. Pass the
   * items explicitly when the caller has just dispatched an edit, because the
   * hook cannot see an un-rendered dispatch.
   */
  flush: (override?: AnnotationItem[]) => void;
  /** Drop a pending debounce without saving. */
  cancelPending: () => void;
  /** Has any write been dispatched for this document, confirmed or not? */
  hasEverSent: () => boolean;
  /** Explicit, user-initiated retry after a failure. Never automatic. */
  retry: () => void;
  /** Take the server's document, abandoning local edits. */
  reloadLatest: () => void;
  reloading: boolean;
};

export function useAutosave({
  designId,
  versionId,
  imageWidth,
  imageHeight,
  schemaVersion,
  state,
  dispatch,
  ready,
}: Params): Autosave {
  const [save, setSave] = useState<SaveStatus>({ status: "saved" });
  const [reloading, setReloading] = useState(false);

  const inFlight = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Whether a write has ever been DISPATCHED for this document, which is not the
  // same question as whether one has been CONFIRMED. A clear needs the first
  // answer: `revision` is still 0 while a first-ever save is in flight, so
  // deciding from the revision alone skips telling the server about a document it
  // may be about to hold.
  const everSent = useRef(false);
  // The reducer state is read inside async callbacks and timers that were
  // created on an earlier render. A ref keeps them reading the CURRENT marks
  // rather than a stale closure, which is what makes the coalesced follow-up
  // write send the latest edits instead of the ones that triggered it.
  const latest = useRef(state);
  latest.current = state;

  const dirty = isDirty(state);

  const runSave = useCallback(async (override?: AnnotationItem[]) => {
    // One write at a time. Note what this means for an OVERRIDE that arrives here
    // during another request: it is dropped, so an editor blur under overlap is
    // saved by the coalesced follow-up rather than immediately — "on blur" becomes
    // "once the open request finishes, plus one debounce interval". The edit itself
    // cannot be lost, because `commitEdit` dispatched it before calling flush, so
    // it is in `latest.current` and the re-armed effect will carry it. Deliberate:
    // queueing the override would mean two writes racing for one revision.
    if (inFlight.current) return;
    const snapshot = latest.current;

    // `override` exists for exactly one reason: a caller that has just dispatched
    // an edit and wants it saved NOW cannot read it back off `latest.current`,
    // because a ref written in the render body only updates on the next render
    // and `dispatch` does not render synchronously. Without this, a note editor
    // blur either no-opped (the pre-edit state was clean) or shipped a payload
    // missing the note that had just been typed — defeating §14's "immediate save
    // on blur" on essentially every commit.
    const sent: AnnotationItem[] = override ?? snapshot.items;
    if (!override && !isDirty(snapshot)) return;
    inFlight.current = true;
    setSave({ status: "saving" });

    const sentAtEpoch = snapshot.epoch;
    everSent.current = true;
    const result = await saveAnnotations(designId, versionId, {
      schema_version: schemaVersion,
      image_width: imageWidth,
      image_height: imageHeight,
      items: sent,
      expected_revision: snapshot.revision,
    });

    inFlight.current = false;

    if (result.ok) {
      dispatch({
        type: "saved",
        sent,
        revision: result.document.revision,
        updatedAt: result.document.updated_at,
        // The reducer drops this if the document was replaced since the request was
        // sent — a clear that landed while this PUT was in flight must not be
        // reverted by its late completion.
        sentAtEpoch,
      });
      // Compared against what was SENT, not via isDirty(latest.current).
      // `dispatch` has not re-rendered yet at this point, so the ref still holds
      // the pre-save state whose baseline is one revision behind — reading
      // isDirty there reported "unsaved" after every successful save and left the
      // pill stuck saying so until the next edit happened to clear it.
      //
      // If edits DID land while this was in flight, the ref was refreshed by
      // those renders, so this correctly stays dirty and the effect below
      // schedules exactly one coalesced follow-up.
      //
      // The second clause covers the third case: a CLEAR landed while this was in
      // flight. That one did re-render, so the ref is current — and its items are
      // legitimately nothing like `sent`, so the comparison alone would leave the
      // pill claiming unsaved changes over a document that is perfectly in step.
      // Asking whether it is actually dirty is the honest question there, and it
      // cannot mislead in the other two cases because both genuinely are dirty.
      const settled = itemsEqual(latest.current.items, sent) || !isDirty(latest.current);
      setSave(settled ? { status: "saved" } : { status: "unsaved" });
      return;
    }

    if (result.kind === "conflict") {
      setSave({ status: "conflict", message: result.message });
      return;
    }
    setSave({ status: "failed", message: result.message });
  }, [designId, versionId, imageWidth, imageHeight, schemaVersion, dispatch]);

  // The debounce. Deliberately does NOT fire while saving, failed or in
  // conflict: those states are cleared by a completed save or by the user, and
  // re-entering here would be the retry loop this file exists to avoid.
  useEffect(() => {
    if (!ready || !dirty) return;
    if (save.status === "saving" || save.status === "failed" || save.status === "conflict") {
      return;
    }
    if (save.status !== "unsaved") setSave({ status: "unsaved" });

    timer.current = setTimeout(() => {
      void runSave();
    }, AUTOSAVE_DEBOUNCE_MS);

    return () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = null;
    };
    // `state.items` rather than the derived `dirty` boolean. `dirty` flips true on
    // the first keystroke and stays true, so the effect never re-ran and the
    // pending timer was never pushed out — a save fired ~800ms after typing
    // STARTED rather than after it stopped, then kept re-firing every
    // debounce-plus-round-trip while the user carried on. Depending on the items
    // makes each edit reset the timer, which is what "after a reasonable idle
    // period" actually means.
  }, [ready, dirty, state.items, save.status, runSave]);

  const flush = useCallback(
    (override?: AnnotationItem[]) => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
      }
      if (save.status === "failed" || save.status === "conflict") return;
      void runSave(override);
    },
    [runSave, save.status],
  );

  // Used before a clear: the DELETE is a second mutation path on the same
  // document, and a debounced PUT landing after it would restore a revision the
  // clear had just retired.
  const cancelPending = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  /** Has any write been dispatched for this document, confirmed or not? */
  const hasEverSent = useCallback(() => everSent.current, []);

  const retry = useCallback(() => {
    // Clearing the status first is what re-arms the debounce effect, so a retry
    // that itself fails lands back in `failed` rather than spinning.
    setSave({ status: "unsaved" });
    void runSave();
  }, [runSave]);

  const reloadLatest = useCallback(() => {
    setReloading(true);
    void (async () => {
      try {
        const document = await fetchAnnotations(designId, versionId);
        dispatch({ type: "hydrate", document });
        setSave({ status: "saved" });
      } catch {
        setSave({
          status: "failed",
          message: "The latest notes could not be loaded. Please try again.",
        });
      } finally {
        setReloading(false);
      }
    })();
  }, [designId, versionId, dispatch]);

  // A hard browser navigation cannot be intercepted with a custom dialog, so
  // this is the browser's own. The in-app warning (§14's anchored popover) is a
  // separate, better-worded control on the back link.
  useEffect(() => {
    if (!dirty) return;
    function warn(event: BeforeUnloadEvent) {
      event.preventDefault();
      return "";
    }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return { save, dirty, flush, cancelPending, hasEverSent, retry, reloadLatest, reloading };
}
