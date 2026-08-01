"use client";

// The colour selector for schema v4's colour questions: a single-choice
// `colour_choice` (the fabric, the embroidery, the dupatta) or a bounded
// multi-select.
//
// One flat wrapping row of swatches, exactly as the handoff draws it — no
// category headings. The manifest's group declaration still fixes the ORDER
// (reds, pinks, golds, greens, blues, purples, pastels), so the palette reads
// as a spectrum rather than an arbitrary shuffle, but it is presentation order
// only and is never announced as a heading.
//
// Every swatch is a REAL radio (single) or checkbox (multi), never a div
// wearing aria-pressed, so the whole row keeps native keyboard and
// screen-reader behaviour. The colour's NAME is carried in the label — visually
// hidden on a circular swatch, as a tooltip on hover, and spelled out in full
// in the chips below — because the handoff's swatch is a bare circle with no
// room for text. An option that is not a colour at all ("Match the fabric")
// gets a labelled pill instead, since there is no hue to show.
//
// A chosen swatch carries a tick and, in multi mode, its order number, so
// selection is never signalled by colour alone. Deselecting never reorders the
// rest.
//
// The design's own colours (the sibling `colour_list` answer) join the same
// row, so a colour added from any question is selectable in all of them —
// matching what the backend's colour_choice validator will accept.

import { COLOUR_GROUP_ORDER, colourSwatch } from "./visuals/manifest";
import { CustomColourPicker } from "./CustomColourPicker";
import type { QuestionOption } from "./types";

type Props = {
  options: QuestionOption[];
  name: string;
  mode: "single" | "multi";
  // Ordered selection. A single-choice question holds at most one entry.
  selected: string[];
  max?: number;
  // Exclusive option values (from the question's constraints) applied with the
  // SAME semantics as every other multi_choice question, so the colour path
  // never silently diverges from the shared exclusivity contract.
  exclusiveValues?: string[];
  // The design's own palette (six-digit lower-case hex), shared across colour
  // questions. Empty when the bride has not added any.
  customColours: string[];
  // Absent when this question does not allow custom colours (`allow_custom`).
  onAddCustomColour?: (hex: string) => void;
  customMax?: number;
  onChange: (next: string[]) => void;
  onBlur?: () => void;
};

type SwatchEntry = { value: string; label: string; hex: string | null };

// The manifest declares the groups in display order, so there is no second
// list to keep in step with it.
const GROUP_ORDER = COLOUR_GROUP_ORDER;

export function ColourSwatchGrid({
  options,
  name,
  mode,
  selected,
  max,
  exclusiveValues,
  customColours,
  onAddCustomColour,
  customMax,
  onChange,
  onBlur,
}: Props) {
  const atMax = mode === "multi" && typeof max === "number" && selected.length >= max;
  const exclusive = new Set(exclusiveValues ?? []);

  const entryFor = (option: QuestionOption): SwatchEntry => ({
    value: option.value,
    label: option.label,
    hex: colourSwatch(option.visual_key)?.hex ?? null,
  });

  // Flatten into one row in manifest-group order. An option whose group is
  // absent from the manifest still renders, at the end, rather than vanishing.
  const ranked = new Map(GROUP_ORDER.map((key, index) => [key, index]));
  const entries: SwatchEntry[] = options
    .map((option, index) => ({
      order: [ranked.get(option.group ?? "") ?? GROUP_ORDER.length, index] as const,
      entry: entryFor(option),
    }))
    .sort((a, b) => a.order[0] - b.order[0] || a.order[1] - b.order[1])
    .map((item) => item.entry);

  // Only a colour_choice answer may be a raw hex. A multi_choice answer is
  // checked against its DECLARED options, so offering the palette there would
  // hand the user a swatch whose selection the backend always rejects.
  if (mode === "single") {
    // A custom colour IS its own value: the answer stored for it is the hex.
    for (const hex of customColours) entries.push({ value: hex, label: hex, hex });
  }

  const entryOf = (value: string): SwatchEntry | undefined =>
    entries.find((candidate) => candidate.value === value);
  const labelFor = (value: string): string => entryOf(value)?.label ?? value;
  const hexFor = (value: string): string | null => entryOf(value)?.hex ?? null;

  const toggle = (value: string, checked: boolean): void => {
    if (mode === "single") {
      onChange(checked ? [value] : []);
      return;
    }
    let next: string[];
    if (checked) {
      if (exclusive.has(value)) {
        // An exclusive value clears everything else.
        next = [value];
      } else {
        // Selecting a normal value removes any exclusive value, and preserves
        // selection ORDER by appending (never reorders the remaining values).
        next = [...selected.filter((entry) => !exclusive.has(entry)), value];
      }
    } else {
      next = selected.filter((entry) => entry !== value);
    }
    onChange(next);
  };

  return (
    <div className="swatch-selector">
      {mode === "multi" ? (
        <p className="field-limit" role="status">
          {typeof max === "number"
            ? `${selected.length} of ${max} chosen`
            : `${selected.length} chosen`}
        </p>
      ) : null}
      <div className="swatch-grid">
        {entries.map((entry) => {
          const checked = selected.includes(entry.value);
          const order = mode === "multi" && checked ? selected.indexOf(entry.value) + 1 : null;
          // Never disable an already-checked swatch or an exclusive one.
          const disabled = !checked && atMax && !exclusive.has(entry.value);
          // No hex means this is not a colour (schema v4's "Match the fabric").
          // A circle with no hue to show would be a swatch pretending to be
          // one, so it becomes a pill that simply says what it is.
          const isColour = entry.hex !== null;
          const className = [
            isColour ? "swatch" : "swatch-pill",
            checked ? "swatch-selected" : "",
            disabled ? "swatch-disabled" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <label
              key={entry.value}
              className={className}
              // Hover affordance for the name a sighted user cannot read off a
              // bare circle. Never the accessible name — that comes from the
              // label's own text below, which assistive technology reads
              // whether or not it is painted.
              title={isColour ? entry.label : undefined}
            >
              <input
                type={mode === "single" ? "radio" : "checkbox"}
                className="visually-hidden"
                name={name}
                value={entry.value}
                checked={checked}
                disabled={disabled}
                onChange={(event) => toggle(entry.value, event.target.checked)}
                onBlur={onBlur}
              />
              {isColour ? (
                <span className="swatch-chip" style={{ background: entry.hex! }} aria-hidden="true">
                  {order ? <span className="swatch-order">{order}</span> : null}
                  {checked ? <span className="swatch-tick">✓</span> : null}
                </span>
              ) : null}
              <span className={isColour ? "swatch-label visually-hidden" : "swatch-label"}>
                {entry.label}
              </span>
              {!isColour && checked ? (
                <span className="swatch-pill-tick" aria-hidden="true">
                  ✓
                </span>
              ) : null}
            </label>
          );
        })}
        {onAddCustomColour ? (
          <CustomColourPicker colours={customColours} max={customMax} onAdd={onAddCustomColour} />
        ) : null}
      </div>
      {selected.length > 0 ? (
        // Below the row, as the handoff has it — and the one place every chosen
        // colour is named in full text rather than shown as a circle.
        <ul className="swatch-summary" aria-label="Selected colours, in order">
          {selected.map((value, index) => (
            <li key={value} className="swatch-summary-item">
              <span
                className="swatch-chip swatch-chip-small"
                style={{ background: hexFor(value) ?? "var(--color-neutral-200)" }}
                aria-hidden="true"
              />
              <span className="swatch-summary-label">
                {mode === "multi" ? `${index + 1}. ` : ""}
                {labelFor(value)}
              </span>
              <button type="button" className="swatch-remove" onClick={() => toggle(value, false)}>
                Remove<span className="visually-hidden"> {labelFor(value)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
