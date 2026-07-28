"use client";

// The grouped colour selector for schema v4's colour questions: a single-choice
// `colour_choice` (the fabric, the embroidery, the dupatta) or a bounded
// multi-select. Colours are grouped by the schema's own bounded `group`
// metadata and ordered by the manifest's group declaration, so the display
// order lives in one place.
//
// Every swatch is a REAL radio (single) or checkbox (multi) with a visible text
// label, a tick when chosen and — in multi mode — an order badge: a choice is
// never conveyed by colour alone, and the whole group keeps native keyboard and
// screen-reader behaviour rather than a div wearing aria-pressed. Deselecting
// never reorders the rest.
//
// The design's own colours (the sibling `colour_list` answer) are offered as an
// extra section, so a colour added from any question is selectable in all of
// them — matching what the backend's colour_choice validator will accept.

import { COLOUR_GROUP_LABELS, colourGroupLabel, colourSwatch } from "./visuals/manifest";
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
type SwatchGroup = { key: string; heading: string; entries: SwatchEntry[] };

// The manifest declares the groups in display order, so there is no second
// list to keep in step with it.
const GROUP_ORDER = Object.keys(COLOUR_GROUP_LABELS);

const CUSTOM_GROUP_KEY = "__custom__";

// A swatch with no hex is not a colour at all (schema v4's "Match the fabric")
// and must not masquerade as one, so it gets a deliberately non-solid fill.
function chipStyle(hex: string | null): React.CSSProperties {
  if (hex === null) {
    return {
      background:
        "linear-gradient(135deg, var(--color-neutral-200) 0 50%, var(--color-neutral-400) 50%)",
    };
  }
  return { background: hex };
}

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

  const groups: SwatchGroup[] = [];
  const placed = new Set<string>();
  for (const key of GROUP_ORDER) {
    const inGroup = options.filter((option) => (option.group ?? "") === key);
    if (inGroup.length === 0) continue;
    groups.push({ key, heading: colourGroupLabel(key), entries: inGroup.map(entryFor) });
    placed.add(key);
  }
  // Any option whose group is absent from the manifest still renders.
  const ungrouped = options.filter((option) => !placed.has(option.group ?? ""));
  if (ungrouped.length > 0) {
    groups.push({ key: "other", heading: "Other", entries: ungrouped.map(entryFor) });
  }
  // Only a colour_choice answer may be a raw hex. A multi_choice answer is
  // checked against its DECLARED options, so offering the palette there would
  // hand the user a swatch whose selection the backend always rejects.
  if (mode === "single" && customColours.length > 0) {
    groups.push({
      key: CUSTOM_GROUP_KEY,
      heading: "Your colours",
      // A custom colour IS its own value: the answer stored for it is the hex.
      entries: customColours.map((hex) => ({ value: hex, label: hex, hex })),
    });
  }

  const labelFor = (value: string): string => {
    for (const group of groups) {
      const entry = group.entries.find((candidate) => candidate.value === value);
      if (entry) return entry.label;
    }
    return value;
  };
  const hexFor = (value: string): string | null => {
    for (const group of groups) {
      const entry = group.entries.find((candidate) => candidate.value === value);
      if (entry) return entry.hex;
    }
    return null;
  };

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
            ? `${selected.length} of ${max} selected`
            : `${selected.length} selected`}
        </p>
      ) : null}
      {selected.length > 0 ? (
        <ul className="swatch-summary" aria-label="Selected colours, in order">
          {selected.map((value, index) => (
            <li key={value} className="swatch-summary-item">
              <span
                className="swatch-chip swatch-chip-small"
                style={chipStyle(hexFor(value))}
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
      {groups.map((group) => (
        <fieldset key={group.key} className="swatch-group">
          <legend className="swatch-group-heading">{group.heading}</legend>
          <div className="swatch-grid">
            {group.entries.map((entry) => {
              const checked = selected.includes(entry.value);
              const order = mode === "multi" && checked ? selected.indexOf(entry.value) + 1 : null;
              // Never disable an already-checked swatch or an exclusive one.
              const disabled = !checked && atMax && !exclusive.has(entry.value);
              return (
                <label
                  key={entry.value}
                  className={`swatch${checked ? " swatch-selected" : ""}${
                    disabled ? " swatch-disabled" : ""
                  }`}
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
                  <span className="swatch-chip" style={chipStyle(entry.hex)} aria-hidden="true">
                    {order ? <span className="swatch-order">{order}</span> : null}
                    {checked ? <span className="swatch-tick">✓</span> : null}
                  </span>
                  <span className="swatch-label">{entry.label}</span>
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
      {onAddCustomColour ? (
        <CustomColourPicker colours={customColours} max={customMax} onAdd={onAddCustomColour} />
      ) : null}
    </div>
  );
}
