"use client";

// A compact, accessible grouped colour selector for the colour_palette
// multi_choice question. Colours are grouped by the schema's bounded `group`
// metadata; the running selected count and an ordered, pinned summary sit above
// the grid so the selection stays visible without scrolling one long column.
// Each swatch is a REAL checkbox with a visible text label and an order badge —
// selection never relies on colour alone. Deselecting a colour never reorders
// the others; unselected swatches disable at the maximum. No native colour
// picker is ever used.

import { colourGroupLabel, colourSwatch } from "./visuals/manifest";
import type { QuestionOption } from "./types";

type Props = {
  options: QuestionOption[];
  name: string;
  selected: string[];
  max: number | undefined;
  // Exclusive option values (from the question's constraints) applied with the
  // SAME semantics as every other multi_choice question, so the colour path
  // never silently diverges from the shared exclusivity contract.
  exclusiveValues: string[];
  onChange: (next: string[]) => void;
  onBlur?: () => void;
};

const GROUP_ORDER = [
  "neutrals",
  "reds",
  "pinks",
  "yellows_metallics",
  "greens",
  "blues_teals",
  "purples",
];

function chipStyle(visualKey: string | undefined): React.CSSProperties {
  const swatch = colourSwatch(visualKey);
  if (swatch?.multicolour) {
    return {
      background:
        "conic-gradient(from 0deg, #c62828, #e8703a, #f2d13a, #3f8a4e, #2f5fb3, #6a3d9a, #c62828)",
    };
  }
  return { background: swatch?.hex ?? "transparent" };
}

export function ColourSwatchGrid({
  options,
  name,
  selected,
  max,
  exclusiveValues,
  onChange,
  onBlur,
}: Props) {
  const atMax = typeof max === "number" && selected.length >= max;
  const exclusive = new Set(exclusiveValues);
  const byValue = new Map(options.map((option) => [option.value, option]));

  const toggle = (value: string, checked: boolean) => {
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

  const groups: { group: string; options: QuestionOption[] }[] = [];
  const seen = new Set<string>();
  for (const group of GROUP_ORDER) {
    const inGroup = options.filter((option) => (option.group ?? "") === group);
    if (inGroup.length > 0) {
      groups.push({ group, options: inGroup });
      seen.add(group);
    }
  }
  // Any option whose group is absent from the fixed order still renders.
  const ungrouped = options.filter((option) => !seen.has(option.group ?? ""));
  if (ungrouped.length > 0) groups.push({ group: "", options: ungrouped });

  return (
    <div className="swatch-selector">
      <p className="swatch-count" aria-live="polite">
        {typeof max === "number"
          ? `${selected.length} of ${max} selected`
          : `${selected.length} selected`}
      </p>
      {selected.length > 0 ? (
        <ul className="swatch-summary" aria-label="Selected colours, in order">
          {selected.map((value, index) => {
            const option = byValue.get(value);
            return (
              <li key={value} className="swatch-summary-item">
                <span className="swatch-chip swatch-chip-small" style={chipStyle(option?.visual_key)} aria-hidden="true" />
                <span className="swatch-summary-label">
                  {index + 1}. {option?.label ?? value}
                </span>
                <button
                  type="button"
                  className="swatch-remove"
                  onClick={() => toggle(value, false)}
                >
                  Remove<span className="visually-hidden"> {option?.label ?? value}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
      {groups.map(({ group, options: groupOptions }) => (
        <fieldset key={group || "other"} className="swatch-group">
          <legend className="swatch-group-heading">
            {group ? colourGroupLabel(group) : "Other"}
          </legend>
          <div className="swatch-grid">
            {groupOptions.map((option) => {
              const checked = selected.includes(option.value);
              const order = checked ? selected.indexOf(option.value) + 1 : null;
              // Never disable an already-checked swatch or an exclusive one.
              const disabled = !checked && atMax && !exclusive.has(option.value);
              return (
                <label
                  key={option.value}
                  className={`swatch${checked ? " swatch-selected" : ""}${
                    disabled ? " swatch-disabled" : ""
                  }`}
                >
                  <input
                    type="checkbox"
                    className="visually-hidden"
                    name={name}
                    value={option.value}
                    checked={checked}
                    disabled={disabled}
                    onChange={(event) => toggle(option.value, event.target.checked)}
                    onBlur={onBlur}
                  />
                  <span className="swatch-chip" style={chipStyle(option.visual_key)} aria-hidden="true">
                    {order ? <span className="swatch-order">{order}</span> : null}
                  </span>
                  <span className="swatch-label">{option.label}</span>
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}
