"use client";

// The annotation workspace.
//
// Marking up a concept never touches the concept: the AI-generated image is
// immutable, the marks are a separate overlay document, and nothing here feeds
// back into refinement. The screen says both of those things out loud, because a
// drawing surface over a design invites exactly the opposite assumption.
//
// Composition only — the parts that can be reasoned about in isolation live
// apart: coordinate maths in `geometry.ts`, editing and undo in `reducer.ts`,
// saving and conflict handling in `useAutosave.ts`.

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AnnotationCanvas, type Tool } from "./AnnotationCanvas";
import { AnnotationList } from "./AnnotationList";
import { ModalDialog } from "./ModalDialog";
import { SavePill } from "./SavePill";
import { SendToAccountButton } from "./SendToAccountButton";
import { ToolRail } from "./ToolRail";
import { nudgeDelta, type Point } from "./geometry";
import {
  DEFAULT_PALETTE,
  MAX_ITEMS,
  NUDGE_STEP,
  NUDGE_STEP_COARSE,
  ZOOM_IN_FACTOR,
  ZOOM_MAX,
  ZOOM_MIN,
  ZOOM_OUT_FACTOR,
  type ItemType,
  type PaletteName,
} from "./limits";
import {
  byCreatedOrder,
  canRedo,
  canUndo,
  createItem,
  editorReducer,
  initialEditorState,
  itemsEqual,
  selectedItem,
} from "./reducer";
import { useAutosave } from "./useAutosave";
import { useAuth } from "@/lib/auth";
import { clearAnnotations, fetchAnnotations, fetchDesignImageUrls, fetchDesignResult } from "@/lib/api";
import type { AnnotationGeometry } from "@/lib/api";

type Props = { designId: string; versionId: string };

export function AnnotationWorkspace({ designId, versionId }: Props) {
  const { user } = useAuth();
  const [state, dispatch] = useReducer(editorReducer, undefined, initialEditorState);

  const [tool, setTool] = useState<Tool>("select");
  const [palette, setPalette] = useState<PaletteName>(DEFAULT_PALETTE);
  const [overlaysVisible, setOverlaysVisible] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftNote, setDraftNote] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const [leaveWarning, setLeaveWarning] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const [limitNotice, setLimitNotice] = useState("");

  const noteFieldRef = useRef<HTMLTextAreaElement | null>(null);
  const keepThemRef = useRef<HTMLButtonElement | null>(null);
  const reloadRef = useRef<HTMLButtonElement | null>(null);
  const backLinkRef = useRef<HTMLAnchorElement | null>(null);

  const resultQuery = useQuery({
    queryKey: ["design-result", designId, versionId],
    queryFn: async () => {
      const outcome = await fetchDesignResult(designId, versionId);
      if (!outcome.ok) throw new Error(outcome.code);
      return outcome.result;
    },
  });

  const imageQuery = useQuery({
    queryKey: ["design-image", designId, versionId],
    queryFn: async () => {
      const outcome = await fetchDesignImageUrls(designId, versionId);
      if (!outcome.ok) throw new Error(outcome.code);
      return outcome.images;
    },
  });

  const annotationsQuery = useQuery({
    queryKey: ["annotations", designId, versionId],
    queryFn: () => fetchAnnotations(designId, versionId),
    // Refetching in the background would replace marks mid-edit. The document is
    // only re-read deliberately: on load, and when resolving a conflict.
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  });

  // Hydrated once, from the server's own canonical dimensions. `revision: 0`
  // with an empty item list is the ordinary before-first-save state, not an
  // error, so there is nothing to special-case here.
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (hydratedRef.current || !annotationsQuery.data) return;
    hydratedRef.current = true;
    dispatch({ type: "hydrate", document: annotationsQuery.data });
  }, [annotationsQuery.data]);

  const document = annotationsQuery.data;
  const imageWidth = document?.image_width ?? 0;
  const imageHeight = document?.image_height ?? 0;

  const autosave = useAutosave({
    designId,
    versionId,
    imageWidth,
    imageHeight,
    schemaVersion: document?.schema_version ?? 1,
    state,
    dispatch,
    ready: hydratedRef.current && imageWidth > 0 && imageHeight > 0,
  });

  const items = useMemo(() => byCreatedOrder(state.items), [state.items]);
  const selected = selectedItem(state);

  // ------------------------------------------------------------------
  // Editing
  // ------------------------------------------------------------------

  // Read through a ref so this callback can be referentially stable.
  //
  // With `[state.items]` as its dependency it was a NEW function on every action
  // that touched the marks — including every pointermove of a drag, which
  // dispatches `nudge` continuously. Because the same `onRequestEdit` reference is
  // handed to every mark, one new closure failed `memo`'s shallow compare for ALL
  // of them, rebuilding up to a hundred SVG groups per move event. That is the cost
  // the memo was added to remove, and stabilising `onSelect` alone did not remove
  // it. The callback only needs the marks as they are when it is actually invoked
  // — a click or an Enter — never as they were when it was defined.
  const itemsRef = useRef(state.items);
  itemsRef.current = state.items;

  const startEdit = useCallback((id: string) => {
    const target = itemsRef.current.find((item) => item.id === id);
    if (!target) return;
    setEditingId(id);
    setDraftNote(target.note);
    // Focus after paint: the textarea does not exist until this render commits.
    requestAnimationFrame(() => noteFieldRef.current?.focus());
  }, []);

  const commitEdit = useCallback(() => {
    if (!editingId) return;
    // Saved immediately rather than after the debounce: a finished note is a
    // deliberate act, and §14 asks for a save on blur of the editor.
    //
    // The merged marks are computed here and handed to `flush` explicitly. The
    // obvious version — dispatch, then `flush()` — silently did nothing useful:
    // `dispatch` does not render synchronously, so the hook's snapshot ref still
    // held the PRE-edit marks and the save either no-opped (nothing looked dirty
    // yet) or wrote a payload missing the note that had just been typed. The
    // save-on-blur was defeated on essentially every commit.
    //
    // The next state is obtained by ASKING the reducer, not by re-deriving what it
    // would do. Calling a shared helper with the same arguments would agree only
    // for as long as `setNote` stays a thin wrapper around that helper; the moment
    // it gained a second concern the immediate save would ship the un-transformed
    // payload while the dispatch produced the right one — the same stale-value bug
    // this fix exists to remove, arriving from the other direction. Running the
    // reducer costs one discarded state object per editor blur.
    const action = { type: "setNote", id: editingId, note: draftNote } as const;
    const committed = editorReducer(state, action).items;
    dispatch(action);
    setEditingId(null);
    // An override bypasses the hook's own dirty check — it has to, since the point
    // is that the edit is not visible there yet. So the "did anything change?"
    // question is answered here instead: opening an editor and closing it without
    // typing must not cost a write.
    if (!itemsEqual(committed, state.items)) autosave.flush(committed);
  }, [editingId, draftNote, state, autosave]);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setDraftNote("");
  }, []);

  // Stable, not inline arrows. `MarkShape` is memoised so a freehand gesture does
  // not rebuild every finished mark on each pointermove, and a fresh closure per
  // render would defeat that memo completely.
  const selectMark = useCallback((id: string | null) => {
    dispatch({ type: "select", id });
  }, []);

  const moveMark = useCallback((id: string, delta: Point) => {
    dispatch({ type: "nudge", id, delta });
  }, []);

  const focusNoteEditor = useCallback(() => {
    // The `N` shortcut and the rail's Note button: edit the selected mark, or the
    // most recent one if nothing is selected. A note needs a mark to point at, so
    // with no marks at all there is nothing to do.
    const target = selected ?? items[items.length - 1];
    if (!target) return;
    dispatch({ type: "select", id: target.id });
    startEdit(target.id);
  }, [selected, items, startEdit]);

  const create = useCallback(
    (type: ItemType, geometry: AnnotationGeometry) => {
      if (state.items.length >= MAX_ITEMS) {
        setLimitNotice(`That is the most marks one concept can hold (${MAX_ITEMS}).`);
        return;
      }
      setLimitNotice("");
      const item = createItem(type, geometry, palette, state.items);
      dispatch({ type: "add", item });
      // Straight into the note editor: §11's empty state promises every mark
      // takes a short note, so the editor is where a new mark lands.
      setEditingId(item.id);
      setDraftNote("");
      requestAnimationFrame(() => noteFieldRef.current?.focus());
    },
    [palette, state.items],
  );

  const nudge = useCallback(
    (id: string, direction: "up" | "down" | "left" | "right", coarse: boolean) => {
      const step = coarse ? NUDGE_STEP_COARSE : NUDGE_STEP;
      const delta = nudgeDelta(step, imageWidth, imageHeight);
      const map = {
        up: { x: 0, y: -delta.y },
        down: { x: 0, y: delta.y },
        left: { x: -delta.x, y: 0 },
        right: { x: delta.x, y: 0 },
      } as const;
      dispatch({ type: "nudge", id, delta: map[direction] });
    },
    [imageWidth, imageHeight],
  );

  const remove = useCallback(
    (id: string) => {
      if (editingId === id) cancelEdit();
      dispatch({ type: "remove", id });
    },
    [editingId, cancelEdit],
  );

  const doClearAll = useCallback(() => {
    setConfirmClear(false);
    cancelEdit();
    // A debounced PUT still on the clock would land after the DELETE and write
    // the marks straight back, at a revision the clear had just retired. Dropping
    // it here closes the common case; a PUT that was ALREADY SENT is handled
    // below, by waiting for it rather than racing it.
    autosave.cancelPending();
    if (state.revision === 0 && !autosave.hasEverSent()) {
      // Nothing has ever been stored AND nothing is on its way, so there is
      // nothing on the server to delete — clearing locally is the whole operation.
      //
      // `revision === 0` alone was not enough. It stays 0 for as long as a
      // first-ever save is in flight, so a clear pressed in that window took this
      // branch and never told the server about the document that write was in the
      // act of creating: the marks stayed on the server, invisible, and came back
      // on the next visit.
      dispatch({ type: "clearAll" });
      return;
    }
    void (async () => {
      // Take the document exclusively BEFORE the DELETE. A PUT already in flight
      // and a DELETE are two independent requests the server may handle in either
      // order, and the losing order silently undoes the clear: the DELETE removes
      // nothing (no row yet), then the PUT's `expected_revision: 0` is treated —
      // correctly, by its own contract — as a first-ever save and re-creates the
      // document holding the marks the user just cleared. Nothing client-side can
      // detect that, so the fix is to never have both in flight at once.
      await autosave.takeExclusive();
      try {
        const outcome = await clearAnnotations(designId, versionId);
        if (!outcome.ok) return;
        // DELETE removes the document itself, so the version is back to its
        // never-annotated state: revision 0, no marks.
        dispatch({
          type: "hydrate",
          document: {
            schema_version: document?.schema_version ?? 1,
            image_width: imageWidth,
            image_height: imageHeight,
            items: [],
            revision: 0,
            updated_at: null,
          },
        });
        // The awaited save may have left a `failed`/`conflict` status behind. It
        // describes a write this clear has just made irrelevant — the server holds
        // nothing and the editor is empty — so leaving it up would show the user a
        // conflict over their own completed clear.
        autosave.markSaved();
      } finally {
        autosave.releaseExclusive();
      }
    })();
  }, [
    designId,
    versionId,
    state.revision,
    document,
    imageWidth,
    imageHeight,
    cancelEdit,
    autosave,
  ]);

  // ------------------------------------------------------------------
  // Keyboard shortcuts
  // ------------------------------------------------------------------

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const inTextField =
        target?.tagName === "TEXTAREA" || target?.tagName === "INPUT" || target?.isContentEditable;

      const modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.key.toLowerCase() === "z") {
        event.preventDefault();
        dispatch({ type: event.shiftKey ? "redo" : "undo" });
        return;
      }

      if (event.key === "Escape") {
        // Layered, most specific first: close the editor, else drop the
        // selection, else return to select mode.
        if (editingId) cancelEdit();
        else if (state.selectedId) dispatch({ type: "select", id: null });
        else if (tool !== "select") setTool("select");
        return;
      }

      // Everything below is a bare letter or symbol, so it must not fire while
      // the user is typing a note.
      if (inTextField || modifier) return;

      const shortcuts: Record<string, () => void> = {
        v: () => setTool("select"),
        p: () => setTool("pin"),
        a: () => setTool("arrow"),
        r: () => setTool("rectangle"),
        f: () => setTool("freehand"),
        n: focusNoteEditor,
        h: () => setOverlaysVisible((visible) => !visible),
        "0": () => setZoom(1),
        "+": () => setZoom((current) => Math.min(ZOOM_MAX, current * ZOOM_IN_FACTOR)),
        "=": () => setZoom((current) => Math.min(ZOOM_MAX, current * ZOOM_IN_FACTOR)),
        "-": () => setZoom((current) => Math.max(ZOOM_MIN, current * ZOOM_OUT_FACTOR)),
      };
      const action = shortcuts[event.key.toLowerCase()];
      if (action) {
        event.preventDefault();
        action();
        return;
      }

      if ((event.key === "Delete" || event.key === "Backspace") && state.selectedId) {
        event.preventDefault();
        remove(state.selectedId);
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [editingId, state.selectedId, tool, cancelEdit, focusNoteEditor, remove]);

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  const result = resultQuery.data;
  const resultRoute = `/design/${designId}/result/${versionId}`;

  if (annotationsQuery.isError) {
    return (
      <div className="annotation-shell-error" role="alert">
        <h1>This concept could not be opened</h1>
        <p>
          It may have been removed, or it may not belong to this workspace.{" "}
          <a href={resultRoute}>Back to the concept</a>
        </p>
      </div>
    );
  }

  if (!document) {
    return (
      <div className="annotation-shell-loading" role="status" aria-live="polite">
        <p>Opening your worktable…</p>
      </div>
    );
  }

  function onBackClick(event: React.MouseEvent<HTMLAnchorElement>) {
    if (!autosave.dirty) return;
    // An anchored popover rather than a modal: this is a warning about one
    // control, so it belongs beside that control rather than over the work.
    event.preventDefault();
    setLeaveWarning(true);
  }

  return (
    <div className="annotation-workspace">
      <header className="annotation-header">
        <div className="annotation-header-lead">
          <a
            className="annotation-back"
            href={resultRoute}
            ref={backLinkRef}
            onClick={onBackClick}
          >
            <span aria-hidden="true">‹</span> Concept
          </a>

          {leaveWarning && (
            <div className="annotation-leave-popover" role="alertdialog" aria-label="Unsaved changes">
              <p>Your last edits haven&rsquo;t saved yet. Leaving now will lose them.</p>
              <div className="annotation-leave-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => {
                    setLeaveWarning(false);
                    backLinkRef.current?.focus();
                  }}
                >
                  Stay
                </button>
                <a className="btn btn-ghost" href={resultRoute}>
                  Leave anyway
                </a>
              </div>
            </div>
          )}

          <span className="annotation-header-divider" aria-hidden="true" />

          <h1 className="annotation-title">Annotate this concept</h1>
          <span className="tag tag-accent-2">Private — only you</span>
        </div>

        <div className="annotation-header-actions">
          <SavePill
            save={autosave.save}
            onRetry={autosave.retry}
            onReview={autosave.reloadLatest}
          />

          <button
            type="button"
            className="btn btn-ghost btn-icon annotation-eye"
            aria-pressed={overlaysVisible}
            title={overlaysVisible ? "Hide annotations (H)" : "Show annotations (H)"}
            aria-label={overlaysVisible ? "Hide annotations (H)" : "Show annotations (H)"}
            onClick={() => setOverlaysVisible((visible) => !visible)}
          >
            <EyeGlyph open={overlaysVisible} />
          </button>

          <SendToAccountButton
            designId={designId}
            versionId={versionId}
            kind="annotated"
            label="Send to account"
            accountEmail={user?.email ?? null}
          />
        </div>
      </header>

      {/* Which version this is, and that its image cannot change — carried
          through from the concept screen because a drawing surface over a design
          invites the assumption that marking it up alters it. */}
      <p className="annotation-identity">
        {result && (
          <>
            <span className="tag tag-neutral">Version {result.version_number}</span>
            <span className={result.is_demo ? "tag tag-accent-2" : "tag tag-accent"}>
              {result.is_demo ? "Demo concept" : "AI-generated concept"}
            </span>
          </>
        )}
        <span className="annotation-identity-note">
          These marks sit on top of a fixed image. The concept itself never changes, and your
          notes are not sent back to the AI or used to refine the design.
        </span>
      </p>

      <div className="annotation-body">
        <ToolRail
          tool={tool}
          onToolChange={setTool}
          onFocusNoteEditor={focusNoteEditor}
          onUndo={() => dispatch({ type: "undo" })}
          onRedo={() => dispatch({ type: "redo" })}
          canUndo={canUndo(state)}
          canRedo={canRedo(state)}
        />

        <div className="annotation-canvas-column">
          <AnnotationCanvas
            imageUrl={imageQuery.data?.original.url ?? null}
            imageWidth={imageWidth}
            imageHeight={imageHeight}
            altText={result?.image_alt_text ?? "Your generated bridalwear concept"}
            items={items}
            selectedId={state.selectedId}
            tool={tool}
            palette={palette}
            overlaysVisible={overlaysVisible}
            zoom={zoom}
            imageFailed={imageFailed}
            imageLoading={imageQuery.isPending || imageQuery.isFetching}
            imageUnavailable={imageQuery.isError}
            onImageError={() => setImageFailed(true)}
            onImageLoad={() => setImageFailed(false)}
            onRetryImage={() => {
              // Clears the element-level failure too, so a retry that succeeds
              // is not still showing the previous attempt's error.
              setImageFailed(false);
              void imageQuery.refetch();
            }}
            onSelect={selectMark}
            onCreate={create}
            onMove={moveMark}
            onRequestEdit={startEdit}
          />

          <div className="annotation-canvas-foot">
            <p className="annotation-instructions">
              Pick a tool, then click or drag on the render. Select a mark to move it with the
              arrow keys — Shift+arrow moves further. Enter edits its note; Escape steps back.
              H hides and shows every mark.
            </p>

            <div className="annotation-zoom" role="group" aria-label="Zoom">
              <button
                type="button"
                className="annotation-zoom-button"
                title="Zoom out (−)"
                aria-label="Zoom out"
                onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z * ZOOM_OUT_FACTOR))}
              >
                −
              </button>
              <span className="annotation-zoom-label">{Math.round(zoom * 100)}%</span>
              <button
                type="button"
                className="annotation-zoom-button"
                title="Zoom in (+)"
                aria-label="Zoom in"
                onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z * ZOOM_IN_FACTOR))}
              >
                +
              </button>
              <span className="annotation-zoom-divider" aria-hidden="true" />
              <button
                type="button"
                className="annotation-zoom-button"
                title="Fit to view (0)"
                aria-label="Fit to view"
                onClick={() => setZoom(1)}
              >
                Fit
              </button>
            </div>

            {zoom !== 1 && (
              <p className="annotation-zoom-hint">
                Drag with Select / Pan to move around · marks stay pinned to the garment
              </p>
            )}

            {limitNotice && (
              <p className="annotation-limit-notice" role="status">
                {limitNotice}
              </p>
            )}
          </div>
        </div>

        <AnnotationList
          items={items}
          selectedId={state.selectedId}
          editingId={editingId}
          draftNote={draftNote}
          onSelect={selectMark}
          onStartEdit={startEdit}
          onCancelEdit={cancelEdit}
          onDraftChange={setDraftNote}
          onCommitEdit={commitEdit}
          onPalette={(id, next) => {
            dispatch({ type: "setPalette", id, palette: next });
            // Remembered for the next mark, so a stylist working in one colour
            // does not re-pick it every time.
            setPalette(next);
          }}
          onDelete={remove}
          onNudge={nudge}
          onClearAll={() => setConfirmClear(true)}
          noteFieldRef={noteFieldRef}
        />
      </div>

      {confirmClear && (
        <ModalDialog
          title={`Clear all ${items.length} annotations?`}
          body={
            <p>
              Every pin, arrow, shape and note on this concept will be removed. This can&rsquo;t
              be undone.
            </p>
          }
          onClose={() => setConfirmClear(false)}
          initialFocusRef={keepThemRef}
        >
          <button
            type="button"
            className="btn btn-ghost"
            ref={keepThemRef}
            onClick={() => setConfirmClear(false)}
          >
            Keep them
          </button>
          <button type="button" className="btn btn-primary is-destructive" onClick={doClearAll}>
            Clear all
          </button>
        </ModalDialog>
      )}

      {autosave.save.status === "conflict" && (
        <ModalDialog
          title="Newer notes elsewhere"
          body={
            <p>
              This concept was annotated somewhere else since your last save. Reload to see the
              latest marks, or discard this tab&rsquo;s unsaved changes — nothing is overwritten
              silently.
            </p>
          }
          // Not dismissible into the wedged state it exists to resolve: closing
          // it has to be a choice between the two copies, so Escape takes the
          // same safe path as Reload.
          onClose={autosave.reloadLatest}
          initialFocusRef={reloadRef}
        >
          <button
            type="button"
            className="btn btn-ghost"
            onClick={autosave.reloadLatest}
            disabled={autosave.reloading}
          >
            Discard my changes
          </button>
          <button
            type="button"
            className="btn btn-primary"
            ref={reloadRef}
            onClick={autosave.reloadLatest}
            disabled={autosave.reloading}
          >
            Reload latest
          </button>
        </ModalDialog>
      )}
    </div>
  );
}

function EyeGlyph({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
      <path
        d="M2 12s3.8-6 10-6 10 6 10 6-3.8 6-10 6-10-6-10-6Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
      />
      <circle cx="12" cy="12" r="2.6" fill="currentColor" />
      {!open && (
        <path d="M4 20 20 4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
      )}
    </svg>
  );
}
