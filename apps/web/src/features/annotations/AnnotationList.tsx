"use client";

// The annotation list — and, deliberately, the whole non-canvas representation
// §13 requires. It is not a summary of the overlay: every editing capability the
// canvas offers is reachable here, because an SVG a pointer draws on cannot be
// the only way to understand or change a mark.
//
// So each row carries the number and type in its accessible name, an editable
// note, labelled palette controls, a delete action, a positional description in
// words, and keyboard nudges. Selecting a row selects the mark and vice versa.

import { useEffect, useRef } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import { AccountSendDisclosure } from "./AccountSendDisclosure";
import { describeGeometry, describeItem } from "./geometry";
import {
  MAX_NOTE_LENGTH,
  PALETTES,
  PALETTE_LABELS,
  TYPE_LABELS,
  type PaletteName,
} from "./limits";
import type { AnnotationItem } from "@/lib/api";

type Props = {
  items: AnnotationItem[];
  selectedId: string | null;
  editingId: string | null;
  draftNote: string;
  onSelect: (id: string) => void;
  onStartEdit: (id: string) => void;
  onCancelEdit: () => void;
  onDraftChange: (note: string) => void;
  onCommitEdit: () => void;
  onPalette: (id: string, palette: PaletteName) => void;
  onDelete: (id: string) => void;
  onNudge: (id: string, direction: "up" | "down" | "left" | "right", coarse: boolean) => void;
  onClearAll: () => void;
  noteFieldRef: React.RefObject<HTMLTextAreaElement | null>;
};

export function AnnotationList({
  items,
  selectedId,
  editingId,
  draftNote,
  onSelect,
  onStartEdit,
  onCancelEdit,
  onDraftChange,
  onCommitEdit,
  onPalette,
  onDelete,
  onNudge,
  onClearAll,
  noteFieldRef,
}: Props) {
  return (
    <aside className="annotation-panel" aria-label="Annotations">
      <div className="annotation-panel-head">
        <h2 className="annotation-panel-title">ANNOTATIONS · {items.length}</h2>
        {items.length > 0 && (
          <button type="button" className="annotation-clear-all" onClick={onClearAll}>
            Clear all
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <p className="annotation-panel-empty">
          Marks you place will list here, numbered to match the canvas.
        </p>
      ) : (
        <ul className="annotation-rows">
          {items.map((item) => (
            <Row
              key={item.id}
              item={item}
              selected={item.id === selectedId}
              editing={item.id === editingId}
              draftNote={draftNote}
              onSelect={onSelect}
              onStartEdit={onStartEdit}
              onCancelEdit={onCancelEdit}
              onDraftChange={onDraftChange}
              onCommitEdit={onCommitEdit}
              onPalette={onPalette}
              onDelete={onDelete}
              onNudge={onNudge}
              noteFieldRef={noteFieldRef}
            />
          ))}
        </ul>
      )}

      {/* The second sentence is §8.5's required disclosure, said before the
          first send rather than buried in a policy page: emailing a render is
          the one action here that takes a private image out of Sitara. It comes
          from a shared component because the concept screen's plain-send button
          needs the same sentence, and two copies would drift. */}
      <p className="annotation-panel-footer">
        These marks are your private worktable. They never appear on shared concept views —
        only in the PNG sent to your account. <AccountSendDisclosure kind="annotated" />
      </p>
    </aside>
  );
}

type RowProps = Omit<Props, "items" | "selectedId" | "editingId" | "onClearAll"> & {
  item: AnnotationItem;
  selected: boolean;
  editing: boolean;
};

function Row({
  item,
  selected,
  editing,
  draftNote,
  onSelect,
  onStartEdit,
  onCancelEdit,
  onDraftChange,
  onCommitEdit,
  onPalette,
  onDelete,
  onNudge,
  noteFieldRef,
}: RowProps) {
  const rowRef = useRef<HTMLLIElement>(null);
  const typeLabel = TYPE_LABELS[item.type];
  const geometryText = describeGeometry(item.geometry);
  const geometryId = `annotation-geometry-${item.id}`;
  const counterId = `annotation-counter-${item.id}`;

  // A row selected from the CANVAS has to come into view here too, or the two
  // representations silently disagree about what is selected. Feature-checked
  // because scrollIntoView is not implemented everywhere (jsdom has none), and
  // scrolling is a convenience — losing it must not break selection itself.
  useEffect(() => {
    if (!selected) return;
    const row = rowRef.current;
    if (typeof row?.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
  }, [selected]);

  function onRowKeyDown(event: ReactKeyboardEvent<HTMLLIElement>) {
    const target = event.target as HTMLElement;
    const inTextField = target.tagName === "TEXTAREA" || target.tagName === "INPUT";

    if (event.key === "Enter" && !inTextField) {
      event.preventDefault();
      onSelect(item.id);
      onStartEdit(item.id);
      return;
    }
    // Delete only outside a text field: inside one, Backspace has to delete a
    // character, not the mark the user is annotating.
    if ((event.key === "Delete" || event.key === "Backspace") && !inTextField) {
      event.preventDefault();
      onDelete(item.id);
      return;
    }
    if (inTextField) return;

    const directions: Record<string, "up" | "down" | "left" | "right"> = {
      ArrowUp: "up",
      ArrowDown: "down",
      ArrowLeft: "left",
      ArrowRight: "right",
    };
    const direction = directions[event.key];
    if (direction) {
      event.preventDefault();
      onSelect(item.id);
      onNudge(item.id, direction, event.shiftKey);
    }
  }

  return (
    <li
      className={`annotation-row${selected ? " is-selected" : ""}`}
      ref={rowRef}
      onKeyDown={onRowKeyDown}
    >
      <div className="annotation-row-head">
        <button
          type="button"
          className="annotation-row-select"
          aria-label={describeItem(item, typeLabel)}
          aria-pressed={selected}
          aria-describedby={geometryId}
          onClick={() => onSelect(item.id)}
        >
          <span className="annotation-row-badge" aria-hidden="true">
            {item.created_order}
          </span>
          <span className="annotation-row-type">{typeLabel}</span>
        </button>

        <button
          type="button"
          className="annotation-row-action"
          aria-label={`Edit note for annotation ${item.created_order}`}
          onClick={() => {
            onSelect(item.id);
            onStartEdit(item.id);
          }}
        >
          <PencilGlyph />
        </button>
        <button
          type="button"
          className="annotation-row-action"
          aria-label={`Delete annotation ${item.created_order}`}
          onClick={() => onDelete(item.id)}
        >
          <TrashGlyph />
        </button>
      </div>

      {!editing && (
        <p className="annotation-row-note">
          {item.note.trim() || <span className="annotation-row-note-empty">No note yet</span>}
        </p>
      )}

      {/* Always rendered, not only while editing: it is the row's positional
          description, and a screen-reader user needs it to know where the mark
          is without ever opening the editor. */}
      <p className="annotation-row-geometry" id={geometryId}>
        {geometryText}
      </p>

      {editing && (
        <div className="annotation-editor">
          <label className="annotation-editor-label" htmlFor={`annotation-note-${item.id}`}>
            Note for annotation {item.created_order}
          </label>
          <textarea
            id={`annotation-note-${item.id}`}
            className="annotation-editor-field"
            ref={noteFieldRef}
            rows={3}
            maxLength={MAX_NOTE_LENGTH}
            value={draftNote}
            aria-describedby={counterId}
            onChange={(event) => onDraftChange(event.target.value)}
            onBlur={onCommitEdit}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.stopPropagation();
                onCancelEdit();
              }
            }}
          />
          <p className="annotation-editor-counter" id={counterId} aria-live="polite">
            {draftNote.length}/{MAX_NOTE_LENGTH}
          </p>

          <fieldset className="annotation-palette">
            <legend>Mark colour</legend>
            {PALETTES.map((palette) => (
              <label key={palette} className={`annotation-swatch annotation-swatch-${palette}`}>
                <input
                  type="radio"
                  name={`palette-${item.id}`}
                  value={palette}
                  checked={item.palette === palette}
                  onChange={() => onPalette(item.id, palette)}
                />
                <span className="annotation-swatch-chip" aria-hidden="true" />
                {/* Never colour alone: the name is the label, and every palette
                    keeps the white halo so contrast holds on any render. */}
                <span className="annotation-swatch-label">{PALETTE_LABELS[palette]}</span>
              </label>
            ))}
          </fieldset>

          <p className="annotation-editor-hint">
            Arrow keys nudge this mark, Shift+arrow moves it further. Escape closes the editor.
          </p>

          <div className="annotation-editor-actions">
            <button type="button" className="btn btn-ghost" onClick={onCancelEdit}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={onCommitEdit}>
              Save
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

function PencilGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <path
        d="M4 20h4l10-10-4-4L4 16v4Zm12-14 2-2 4 4-2 2-4-4Z"
        fill="currentColor"
      />
    </svg>
  );
}

function TrashGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <path
        d="M6 7h12l-1 13H7L6 7Zm3-3h6l1 2H8l1-2Z"
        fill="currentColor"
      />
    </svg>
  );
}
