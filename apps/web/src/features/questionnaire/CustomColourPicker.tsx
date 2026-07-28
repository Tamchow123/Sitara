"use client";

// The handoff's "Any colour" control: a dashed pill that opens an inline panel
// holding a native <input type="color">, the live hex, one line of guidance and
// Add/Cancel. Added colours join the design's own palette and become selectable
// swatches in every colour question.
//
// The value written into the answer is always a six-digit LOWER-CASE hex — the
// exact shape the backend's colour_list validator accepts — so a browser that
// reports "#AABBCC" cannot produce an answer the API will reject.

import { useId, useState } from "react";

type Props = {
  // The design's current palette (ordered, already normalised).
  colours: string[];
  // Bound from the schema's colour_list max_items — never a hard-coded number.
  // Absent means unbounded HERE; the backend still enforces its own ceiling.
  // Deliberately not defaulted to the palette's own length, which would report
  // a full palette at every size and silently disable adding.
  max?: number;
  onAdd: (hex: string) => void;
};

const SIX_DIGIT_HEX = /^#[0-9a-f]{6}$/;
const DEFAULT_DRAFT = "#c67139";

export function CustomColourPicker({ colours, max, onAdd }: Props) {
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(DEFAULT_DRAFT);
  const [error, setError] = useState<string | null>(null);
  const full = typeof max === "number" && colours.length >= max;

  const close = () => {
    setOpen(false);
    setError(null);
  };

  const add = () => {
    const hex = draft.trim().toLowerCase();
    if (!SIX_DIGIT_HEX.test(hex)) {
      setError("Please choose a colour first.");
      return;
    }
    if (colours.includes(hex)) {
      setError("That colour is already in your colours.");
      return;
    }
    onAdd(hex);
    close();
  };

  return (
    <div className="custom-colour">
      <button
        type="button"
        className="custom-colour-pill"
        aria-expanded={open}
        aria-controls={panelId}
        disabled={full}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <span className="custom-colour-dot" aria-hidden="true" />
        Any colour
      </button>
      {full ? (
        <p className="field-limit" role="status">
          You have added the maximum of {max} colours.
        </p>
      ) : null}
      {open && !full ? (
        <div className="custom-colour-panel" id={panelId}>
          <label className="custom-colour-input">
            Pick a colour
            <input
              type="color"
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                setError(null);
              }}
            />
          </label>
          {/* The hex in text, so the choice is never conveyed by colour alone. */}
          <p className="custom-colour-hex">{draft.toLowerCase()}</p>
          <p className="custom-colour-guidance">
            Added colours stay available across every colour question in your brief.
          </p>
          {error ? (
            <p className="field-error" role="alert">
              {error}
            </p>
          ) : null}
          <div className="custom-colour-actions">
            <button type="button" className="custom-colour-add" onClick={add}>
              Add
            </button>
            <button type="button" className="custom-colour-cancel" onClick={close}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
